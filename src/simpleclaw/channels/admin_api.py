"""Admin Backend REST API — Admin UI(BIZ-37)를 백킹하는 데몬 측 엔드포인트.

aiohttp 기반의 로컬 HTTP 서버로, ``127.0.0.1:8082`` 기본 바인딩이며 Bearer 토큰
인증을 강제한다. 12개 설정 영역 + 시크릿 + 감사 + 헬스 + 시스템 액션을 다룬다.

엔드포인트 요약:

- ``GET    /admin/v1/config``                 — 전체 머지된 설정(시크릿 마스킹)
- ``GET    /admin/v1/config/{area}``          — 영역별 설정
- ``PATCH  /admin/v1/config/{area}``          — 변경 (``?dry_run=true`` 지원)
- ``GET    /admin/v1/secrets``                — 시크릿 메타데이터
- ``POST   /admin/v1/secrets/{name}/reveal``  — 일회성 평문 (15s TTL nonce)
- ``POST   /admin/v1/secrets/{name}/rotate``  — 회전
- ``POST   /admin/v1/secrets/master/rotate``  — 마스터 키 회전 + 재암호화
- ``GET    /admin/v1/audit``                  — 감사 로그 검색
- ``POST   /admin/v1/audit/{id}/undo``        — 변경 되돌리기
- ``GET    /admin/v1/logs``                   — 구조화 로그 검색
- ``GET    /admin/v1/health``                 — 헬스 스냅샷
- ``GET    /admin/v1/system/info``            — 버전·PID·uptime·디스크·DB 경로 등 진단 정보
- ``POST   /admin/v1/system/restart``         — 데몬 재시작 요청

설계 결정:

- **인증은 토큰 1개**: 단일 운영자 가정(admin-requirements.md §3.1)에 따라 keyring
  ``admin_api_token``과 비교. mTLS 전환은 후속 이슈(BIZ-별도)로 보류.
- **Process-restart 키는 즉시 적용 X**: pending 변경 파일에 적재해 데몬 재시작 시
  반영하도록 위임. 응답에 ``requires_restart=True`` + ``affected_modules`` 동봉.
- **시크릿 마스킹**: 응답/감사 로그 모두 시크릿 키 패턴 값을 자동 마스킹. 단,
  ``env:``/``keyring:``/``file:`` 참조 자체는 비밀이 아니므로 원형 유지.
- **dry-run**: ``?dry_run=true``는 검증 + 정책 분석 + diff만 반환하고 파일/볼트는
  건드리지 않는다. 검증 실패 시 422 + 필드별 에러 목록.
- **Reveal nonce**: 15초 TTL로 일회 평문을 발급. nonce 한 번 사용되면 즉시 폐기.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from aiohttp import web

from simpleclaw.channels.admin_audit import (
    AuditEntry,
    AuditLog,
    _mask_secrets,
)
from simpleclaw.channels.admin_policy import (
    HOT,
    PROCESS_RESTART,
    PolicyResult,
    classify_keys,
    validate_patch,
)
from simpleclaw.security.secrets import (
    EncryptedFileBackend,
    SecretBackend,
    SecretsError,
    SecretsManager,
)

logger = logging.getLogger(__name__)


# 마스터 시크릿 회전 시 노출되는 백엔드 라벨 — 응답/감사용.
_BACKEND_LABELS = ("env", "keyring", "file")

# 영역 이름 → config.yaml 최상위 키 매핑. ``recipes``/``logging``/``audit``은
# 별도 엔드포인트가 있거나 read-only라 PATCH가 허용되지 않는다.
AREA_TO_YAML_KEY: dict[str, str | list[str]] = {
    "llm": "llm",
    "agent": "agent",
    "memory": "memory",
    "security": "security",
    "skills": "skills",
    "mcp": "mcp",
    "voice": "voice",
    "telegram": "telegram",
    "webhook": "webhook",
    "channels": ["telegram", "webhook"],  # 그룹 별칭
    "sub_agents": "sub_agents",
    "daemon": "daemon",
    "cron": "daemon.cron_retry",  # admin-requirements §1 11번
    "persona": "persona",
    "system": "daemon",  # 호스트/포트/DB 경로 같은 process-restart 키 모음
}


# ---------------------------------------------------------------------------
# 헬퍼: 깊은 머지 / dotted key 접근
# ---------------------------------------------------------------------------


def _deep_merge(dst: dict, src: dict) -> dict:
    """``src``의 키를 ``dst``에 깊은 병합한다 — 리스트는 통째로 교체.

    PATCH 의미를 따라 dict는 부분 갱신, 그 외(리스트/스칼라)는 덮어쓴다.
    """
    for k, v in src.items():
        if (
            k in dst
            and isinstance(dst[k], dict)
            and isinstance(v, dict)
        ):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _get_dotted(d: dict, dotted: str) -> Any:
    """``a.b.c`` 경로로 dict 트리에서 값을 꺼낸다. 없으면 ``None``."""
    parts = dotted.split(".")
    cur: Any = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set_dotted(d: dict, dotted: str, value: Any) -> None:
    """``a.b.c`` 경로로 dict 트리에 값을 설정한다 — 중간 dict는 자동 생성."""
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _mask_for_response(payload: object) -> object:
    """응답 본문에서 시크릿을 마스킹한다.

    ``_mask_secrets``의 얇은 래퍼 — 응답 시 추가 정책이 필요하면 여기서 확장한다.
    """
    return _mask_secrets(payload)


# ---------------------------------------------------------------------------
# Reveal nonce 저장소
# ---------------------------------------------------------------------------


@dataclass
class _RevealEntry:
    """일회성 reveal 토큰 — TTL 만료 또는 1회 사용 시 폐기."""

    name: str
    backend: str
    expires_at: float


# ---------------------------------------------------------------------------
# 펜딩 변경 저장소
# ---------------------------------------------------------------------------


def _pending_changes_path(base_dir: Path) -> Path:
    """``Process-restart`` 정책 변경의 적재 위치 — 데몬 재시작 시 머지된다."""
    return base_dir / "pending_changes.yaml"


def _load_pending(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_pending(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# 서버
# ---------------------------------------------------------------------------


@dataclass
class AdminAPIMetrics:
    """Admin API 호출 카운터 — 대시보드/알림 데이터 소스."""

    requests: int = 0
    blocked_unauthorized: int = 0
    config_patches: int = 0
    config_dry_runs: int = 0
    secret_reveals: int = 0
    secret_rotations: int = 0
    master_key_rotations: int = 0
    audit_undos: int = 0
    pending_changes: int = 0
    rejected: int = 0  # 422 검증 실패


# 콜백 — 시스템 재시작 트리거. 호출자(run_bot.py 등)가 실제 재시작 메커니즘을 주입.
RestartCallback = Callable[[dict], "Awaitable[None] | None"]
ReloadCallback = Callable[[str, dict], "Awaitable[None] | None"]


class AdminAPIServer:
    """Admin UI 백엔드 REST 서버 — 단일 운영자, 로컬 바인딩 가정.

    의존성을 모두 생성자 주입 받아 단위 테스트에서 독립 인스턴스를 만들기 쉽다.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8082,
        auth_token: str = "",
        config_path: str | Path,
        audit_log: AuditLog | None = None,
        secrets_manager: SecretsManager | None = None,
        admin_state_dir: str | Path | None = None,
        restart_callback: RestartCallback | None = None,
        reload_callback: ReloadCallback | None = None,
        structured_logger: object | None = None,
        health_provider: Callable[[], dict] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._config_path = Path(config_path)

        # 상태 저장 위치(펜딩 변경, reveal nonce 등) — 시크릿 볼트와 분리해
        # ``~/.simpleclaw/admin/``에 둔다.
        if admin_state_dir is None:
            admin_state_dir = Path.home() / ".simpleclaw" / "admin"
        self._state_dir = Path(admin_state_dir)

        self._audit = audit_log if audit_log is not None else AuditLog()
        self._secrets = (
            secrets_manager if secrets_manager is not None else SecretsManager()
        )
        self._restart_cb = restart_callback
        self._reload_cb = reload_callback
        self._structured = structured_logger
        self._health_provider = health_provider

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._running = False

        # 서버 부팅 시각 — 헬스 응답의 uptime 계산용.
        self._started_at: float = 0.0

        # reveal nonce 저장소 — 메모리에만 보관(데몬 재시작 시 무효화).
        self._reveal_nonces: dict[str, _RevealEntry] = {}
        self._reveal_ttl_seconds: float = 15.0

        self._metrics = AdminAPIMetrics()

    # ------------------------------------------------------------------
    # 라이프사이클
    # ------------------------------------------------------------------

    def get_app(self) -> web.Application:
        """라우트가 구성된 aiohttp Application을 반환한다.

        테스트는 이 앱을 ``aiohttp_client``에 직접 물려 ``start()``를 우회한다.
        """
        if self._app is not None:
            return self._app

        app = web.Application()
        prefix = "/admin/v1"
        app.router.add_get(f"{prefix}/config", self._wrap(self._handle_get_config_all))
        app.router.add_get(
            f"{prefix}/config/{{area}}", self._wrap(self._handle_get_config_area)
        )
        app.router.add_patch(
            f"{prefix}/config/{{area}}", self._wrap(self._handle_patch_config_area)
        )
        app.router.add_get(f"{prefix}/secrets", self._wrap(self._handle_list_secrets))
        app.router.add_post(
            f"{prefix}/secrets/master/rotate",
            self._wrap(self._handle_rotate_master_secret),
        )
        app.router.add_post(
            f"{prefix}/secrets/{{name}}/reveal", self._wrap(self._handle_reveal_secret)
        )
        app.router.add_post(
            f"{prefix}/secrets/{{name}}/rotate", self._wrap(self._handle_rotate_secret)
        )
        app.router.add_get(f"{prefix}/audit", self._wrap(self._handle_search_audit))
        app.router.add_post(
            f"{prefix}/audit/{{id}}/undo", self._wrap(self._handle_undo_audit)
        )
        app.router.add_get(f"{prefix}/logs", self._wrap(self._handle_search_logs))
        app.router.add_get(f"{prefix}/health", self._wrap(self._handle_health))
        app.router.add_get(
            f"{prefix}/system/info", self._wrap(self._handle_system_info)
        )
        app.router.add_post(
            f"{prefix}/system/restart", self._wrap(self._handle_system_restart)
        )

        self._app = app
        return app

    async def start(self) -> None:
        """HTTP 서버를 시작한다."""
        app = self.get_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        self._running = True
        self._started_at = time.time()
        logger.info("Admin API server started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        self._running = False
        logger.info("Admin API server stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_metrics(self) -> AdminAPIMetrics:
        return AdminAPIMetrics(**self._metrics.__dict__)

    # ------------------------------------------------------------------
    # 인증 래퍼 — 모든 핸들러는 이 래퍼를 통과해 토큰을 검증받는다.
    # ------------------------------------------------------------------

    def _wrap(
        self,
        handler: Callable[[web.Request], "Awaitable[web.StreamResponse]"],
    ) -> Callable[[web.Request], "Awaitable[web.StreamResponse]"]:
        async def wrapped(request: web.Request) -> web.StreamResponse:
            self._metrics.requests += 1
            if self._auth_token:
                got = request.headers.get("Authorization", "")
                expected = f"Bearer {self._auth_token}"
                # secrets.compare_digest으로 타이밍 공격 안전.
                if not secrets.compare_digest(got, expected):
                    self._metrics.blocked_unauthorized += 1
                    return _json_error(401, "Unauthorized")
            try:
                return await handler(request)
            except web.HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Admin API handler failed: %s", exc)
                return _json_error(500, f"Internal error: {exc}")

        return wrapped

    # ------------------------------------------------------------------
    # 설정 — GET
    # ------------------------------------------------------------------

    def _read_yaml(self) -> dict:
        """현재 ``config.yaml``을 읽어 dict로 반환한다 (없으면 빈 dict)."""
        if not self._config_path.is_file():
            return {}
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("config.yaml 읽기 실패: %s", exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _write_yaml(self, data: dict) -> None:
        """``config.yaml``을 atomic하게 다시 쓴다 (백업 ``config.yaml.{ts}.bak``).

        admin-requirements §4.3 — 편집 시 백업 자동 생성, 최근 10개 보존.
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        # 백업 — 기존 파일이 있으면 ts 붙여서 보존.
        if self._config_path.is_file():
            ts = time.strftime("%Y%m%d-%H%M%S")
            bak = self._config_path.with_suffix(
                self._config_path.suffix + f".{ts}.bak"
            )
            try:
                bak.write_bytes(self._config_path.read_bytes())
            except OSError:
                pass
            self._prune_backups()

        tmp = self._config_path.with_suffix(self._config_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        tmp.replace(self._config_path)

    def _prune_backups(self, keep: int = 10) -> None:
        """오래된 ``config.yaml.{ts}.bak`` 파일을 keep개만 남기고 정리."""
        parent = self._config_path.parent
        if not parent.is_dir():
            return
        baks = sorted(parent.glob(f"{self._config_path.name}.*.bak"))
        excess = len(baks) - keep
        if excess <= 0:
            return
        for old in baks[:excess]:
            try:
                old.unlink()
            except OSError:
                pass

    async def _handle_get_config_all(self, request: web.Request) -> web.Response:
        """전체 머지된 설정 — 시크릿은 마스킹된 ref/문자열 형태로 노출."""
        data = self._read_yaml()
        return _json_ok({"config": _mask_for_response(data)})

    async def _handle_get_config_area(self, request: web.Request) -> web.Response:
        area = request.match_info["area"]
        if area not in AREA_TO_YAML_KEY:
            return _json_error(404, f"Unknown area: {area}")
        data = self._read_yaml()
        target = self._extract_area(data, area)
        return _json_ok({"area": area, "config": _mask_for_response(target)})

    def _extract_area(self, full: dict, area: str) -> Any:
        """영역 이름에 매핑된 YAML 키(들)에서 부분 트리를 추출한다."""
        mapping = AREA_TO_YAML_KEY[area]
        if isinstance(mapping, list):
            return {key: full.get(key, {}) for key in mapping}
        return _get_dotted(full, mapping) or {}

    # ------------------------------------------------------------------
    # 설정 — PATCH (dry-run / 적용 / pending)
    # ------------------------------------------------------------------

    async def _handle_patch_config_area(self, request: web.Request) -> web.Response:
        area = request.match_info["area"]
        if area not in AREA_TO_YAML_KEY:
            return _json_error(404, f"Unknown area: {area}")

        try:
            patch = await request.json()
        except (json.JSONDecodeError, Exception):
            return _json_error(400, "Invalid JSON payload")
        if not isinstance(patch, dict):
            return _json_error(400, "Patch must be a JSON object")

        # 1) 검증 — 422 fast-fail.
        errors = validate_patch(area, patch)
        if errors:
            self._metrics.rejected += 1
            self._audit.append(
                action="config.update",
                area=area,
                target=area,
                before=None,
                after=patch,
                outcome="rejected",
                requires_restart=False,
                undoable=False,
                reason="; ".join(errors),
                actor_id=_actor_from(request),
                trace_id=request.headers.get("X-Trace-Id", ""),
            )
            return _json_error(422, "Validation failed", details={"errors": errors})

        # 2) 정책 분석 — Hot/Service-restart/Process-restart.
        policy = classify_keys(area, patch)

        # 3) before 스냅샷 + diff 계산.
        full = self._read_yaml()
        before_snap = _project(full, area, patch)

        # 4) dry-run 처리 — 파일/볼트 미수정.
        dry_run = _truthy_query(request, "dry_run")
        if dry_run:
            self._metrics.config_dry_runs += 1
            self._audit.append(
                action="config.update",
                area=area,
                target=area,
                before=before_snap,
                after=patch,
                outcome="dry_run",
                requires_restart=policy.requires_restart,
                affected_modules=policy.affected_modules,
                undoable=False,
                actor_id=_actor_from(request),
                trace_id=request.headers.get("X-Trace-Id", ""),
            )
            return _json_ok(
                {
                    "outcome": "dry_run",
                    "diff": {
                        "before": _mask_for_response(before_snap),
                        "after": _mask_for_response(patch),
                    },
                    "policy": _policy_to_dict(policy),
                }
            )

        # 5) Process-restart는 즉시 반영 X — 펜딩 적재.
        if policy.level == PROCESS_RESTART:
            pending_path = _pending_changes_path(self._state_dir)
            pending = _load_pending(pending_path)
            self._merge_patch_into_full(pending, area, patch)
            _save_pending(pending_path, pending)

            self._metrics.pending_changes += 1
            entry = self._audit.append(
                action="config.update",
                area=area,
                target=area,
                before=before_snap,
                after=patch,
                outcome="pending",
                requires_restart=True,
                affected_modules=policy.affected_modules,
                undoable=True,
                actor_id=_actor_from(request),
                trace_id=request.headers.get("X-Trace-Id", ""),
            )
            return _json_ok(
                {
                    "outcome": "pending",
                    "audit_id": entry.id,
                    "policy": _policy_to_dict(policy),
                    "message": "데몬 재시작 후 적용됩니다.",
                }
            )

        # 6) Hot / Service-restart — yaml 즉시 반영.
        self._merge_patch_into_full(full, area, patch)
        self._write_yaml(full)
        self._metrics.config_patches += 1

        # Hot이면 reload 콜백 호출 — 등록되지 않았으면 lazy loader가 처리.
        if policy.level == HOT and self._reload_cb is not None:
            try:
                result = self._reload_cb(area, patch)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[func-returns-value]
            except Exception:  # noqa: BLE001
                logger.exception("reload callback failed for area=%s", area)

        entry = self._audit.append(
            action="config.update",
            area=area,
            target=area,
            before=before_snap,
            after=patch,
            outcome="applied",
            requires_restart=policy.requires_restart,
            affected_modules=policy.affected_modules,
            undoable=True,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )

        return _json_ok(
            {
                "outcome": "applied",
                "audit_id": entry.id,
                "policy": _policy_to_dict(policy),
            }
        )

    def _merge_patch_into_full(
        self, full: dict, area: str, patch: dict
    ) -> None:
        """``area`` 매핑을 따라 ``full`` 트리에 ``patch``를 깊은 머지한다.

        ``channels`` 같은 그룹 별칭은 patch의 최상위 키별로 분기하고,
        ``daemon.cron_retry`` 같은 dotted 매핑은 해당 위치를 정확히 가리킨다.
        """
        mapping = AREA_TO_YAML_KEY[area]
        if isinstance(mapping, list):
            for key in mapping:
                if key in patch and isinstance(patch[key], dict):
                    sub = full.setdefault(key, {})
                    if not isinstance(sub, dict):
                        sub = {}
                        full[key] = sub
                    _deep_merge(sub, patch[key])
                elif key in patch:
                    full[key] = copy.deepcopy(patch[key])
            return

        # dotted path 매핑
        if "." in mapping:
            existing = _get_dotted(full, mapping)
            if not isinstance(existing, dict):
                existing = {}
            _deep_merge(existing, patch)
            _set_dotted(full, mapping, existing)
            return

        # 단일 최상위 키
        sub = full.setdefault(mapping, {})
        if not isinstance(sub, dict):
            sub = {}
            full[mapping] = sub
        _deep_merge(sub, patch)

    # ------------------------------------------------------------------
    # 시크릿 — 메타데이터 / reveal / rotate / master rotate
    # ------------------------------------------------------------------

    async def _handle_list_secrets(self, request: web.Request) -> web.Response:
        """백엔드별로 등록된 시크릿 메타데이터 — 이름·마지막 회전 시각만."""
        items: list[dict] = []
        for backend_name in _BACKEND_LABELS:
            try:
                backend = self._secrets.get_backend(backend_name)
            except SecretsError:
                continue
            try:
                names = _list_backend_keys(backend)
            except SecretsError as exc:
                logger.warning(
                    "백엔드 %s 키 목록 조회 실패: %s", backend_name, exc
                )
                names = []
            for name in names:
                items.append(
                    {
                        "name": name,
                        "backend": backend_name,
                        "last_rotated_at": _last_rotated_for(self._audit, name),
                    }
                )
        return _json_ok({"secrets": items})

    async def _handle_reveal_secret(self, request: web.Request) -> web.Response:
        """시크릿 평문을 일회성 nonce와 함께 반환 — 15초 TTL."""
        name = request.match_info["name"]
        backend_name = request.query.get("backend", "")
        backend, value = self._lookup_secret(name, backend_name)
        if value is None:
            return _json_error(404, f"Secret not found: {name}")

        nonce = secrets.token_urlsafe(24)
        self._gc_nonces()
        self._reveal_nonces[nonce] = _RevealEntry(
            name=name,
            backend=backend,
            expires_at=time.monotonic() + self._reveal_ttl_seconds,
        )
        self._metrics.secret_reveals += 1
        self._audit.append(
            action="secret.reveal",
            area="secrets",
            target=f"{backend}:{name}",
            before=None,
            after=None,
            outcome="applied",
            requires_restart=False,
            undoable=False,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok(
            {
                "name": name,
                "backend": backend,
                "value": value,
                "nonce": nonce,
                "expires_in_seconds": int(self._reveal_ttl_seconds),
            }
        )

    async def _handle_rotate_secret(self, request: web.Request) -> web.Response:
        """시크릿을 새 값으로 회전 — 본문 ``{"value": "...", "backend": "..."}``."""
        name = request.match_info["name"]
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return _json_error(400, "Invalid JSON payload")
        if not isinstance(body, dict) or "value" not in body:
            return _json_error(400, "Missing field: value")
        backend = str(body.get("backend") or _detect_backend(self._secrets, name) or "keyring")
        value = body["value"]
        if not isinstance(value, str) or not value:
            return _json_error(400, "value must be non-empty string")

        try:
            self._secrets.store(backend, name, value)
        except SecretsError as exc:
            return _json_error(400, str(exc))

        self._metrics.secret_rotations += 1
        # 시크릿 회전의 ``after``는 평문 자체이므로 키 이름과 무관하게 강제 마스킹.
        # ``_mask_secrets``는 키 이름 기반이라 ``value`` 같은 평범한 키를 잡지 못하므로,
        # 여기서 회전 의미를 알고 있는 핸들러가 마스킹을 책임진다.
        from simpleclaw.channels.admin_audit import _mask_value

        entry = self._audit.append(
            action="secret.rotate",
            area="secrets",
            target=f"{backend}:{name}",
            before=None,
            after={"value": _mask_value(value)},
            outcome="applied",
            requires_restart=False,
            undoable=False,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok(
            {"outcome": "applied", "audit_id": entry.id, "backend": backend, "name": name}
        )

    async def _handle_rotate_master_secret(self, request: web.Request) -> web.Response:
        """마스터 키를 회전하고 모든 file 백엔드 시크릿을 재암호화한다.

        절차:
        1) 현재 마스터 키로 모든 ``file:`` 시크릿을 해독해 메모리에 보관
        2) 새 마스터 키 생성·저장 (이전 키는 ``master.key.{ts}.bak``으로 백업)
        3) 메모리 평문을 새 키로 다시 암호화해 볼트에 저장
        """
        try:
            file_backend = self._secrets.get_backend("file")
        except SecretsError as exc:
            return _json_error(400, str(exc))
        if not isinstance(file_backend, EncryptedFileBackend):
            return _json_error(400, "file backend is not an EncryptedFileBackend")

        try:
            count = _rotate_master_key(file_backend)
        except SecretsError as exc:
            return _json_error(500, f"Master key rotation failed: {exc}")

        self._metrics.master_key_rotations += 1
        entry = self._audit.append(
            action="secret.rotate_master",
            area="secrets",
            target="master_key",
            before=None,
            after={"reencrypted_count": count},
            outcome="applied",
            requires_restart=False,
            undoable=False,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok(
            {"outcome": "applied", "reencrypted_count": count, "audit_id": entry.id}
        )

    def _lookup_secret(
        self, name: str, backend_name: str
    ) -> tuple[str, str | None]:
        """이름과 (옵셔널) 백엔드를 받아 평문 값을 반환한다.

        백엔드를 명시하지 않으면 ``env`` → ``keyring`` → ``file`` 순으로 탐색한다.
        """
        if backend_name:
            try:
                backend = self._secrets.get_backend(backend_name)
            except SecretsError:
                return backend_name, None
            try:
                return backend_name, backend.get(name)
            except SecretsError:
                return backend_name, None

        for label in _BACKEND_LABELS:
            try:
                backend = self._secrets.get_backend(label)
            except SecretsError:
                continue
            try:
                value = backend.get(name)
            except SecretsError:
                continue
            if value is not None:
                return label, value
        return "", None

    def _gc_nonces(self) -> None:
        """만료된 reveal nonce를 정리한다 — TTL 지난 항목만 제거."""
        now = time.monotonic()
        expired = [n for n, e in self._reveal_nonces.items() if e.expires_at < now]
        for n in expired:
            self._reveal_nonces.pop(n, None)

    # ------------------------------------------------------------------
    # 감사 — 검색 / undo
    # ------------------------------------------------------------------

    async def _handle_search_audit(self, request: web.Request) -> web.Response:
        q = request.query
        try:
            limit = int(q.get("limit", "200"))
        except ValueError:
            limit = 200
        entries = self._audit.search(
            since=q.get("since"),
            actor=q.get("actor"),
            area=q.get("area"),
            outcome=q.get("outcome"),
            action=q.get("action"),
            limit=limit,
        )
        return _json_ok(
            {"entries": [_audit_to_dict(e) for e in entries]}
        )

    async def _handle_undo_audit(self, request: web.Request) -> web.Response:
        entry_id = request.match_info["id"]
        target = self._audit.get(entry_id)
        if target is None:
            return _json_error(404, f"Audit entry not found: {entry_id}")
        if not target.undoable:
            return _json_error(409, "Entry is not undoable")
        if target.action != "config.update":
            return _json_error(409, "Only config.update entries are undoable")
        if target.outcome not in ("applied", "pending"):
            return _json_error(409, f"Cannot undo outcome={target.outcome}")
        if not isinstance(target.before, dict):
            return _json_error(409, "Audit entry has no restorable 'before' snapshot")

        # before를 새 PATCH로 적용 — 결과는 새 audit entry로 기록(이력 보존).
        full = self._read_yaml()
        self._merge_patch_into_full(full, target.area, target.before)
        self._write_yaml(full)

        # 펜딩 항목이라면 펜딩 파일에서도 제거 — 정확한 삭제는 어려우니
        # 단순히 같은 패치 트리를 펜딩에서 빼낸다.
        pending_path = _pending_changes_path(self._state_dir)
        pending = _load_pending(pending_path)
        if pending:
            try:
                self._merge_patch_into_full(pending, target.area, target.before)
                _save_pending(pending_path, pending)
            except Exception:  # noqa: BLE001
                pass

        if self._reload_cb is not None:
            try:
                result = self._reload_cb(target.area, target.before)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[func-returns-value]
            except Exception:  # noqa: BLE001
                logger.exception("reload callback failed during undo")

        self._metrics.audit_undos += 1
        new_entry = self._audit.append(
            action="config.update",
            area=target.area,
            target=target.target,
            before=target.after,  # 의미상 현재값 → 이전값으로 되돌림
            after=target.before,
            outcome="applied",
            requires_restart=False,
            undoable=True,
            reason=f"undo of {entry_id}",
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok({"outcome": "applied", "audit_id": new_entry.id})

    # ------------------------------------------------------------------
    # 로그 / 헬스 / 시스템
    # ------------------------------------------------------------------

    async def _handle_search_logs(self, request: web.Request) -> web.Response:
        """주입된 ``StructuredLogger``로부터 로그 항목을 조회한다."""
        slog = self._structured
        if slog is None:
            return _json_ok({"entries": []})
        get_entries = getattr(slog, "get_entries", None)
        if not callable(get_entries):
            return _json_ok({"entries": []})

        q = request.query
        kwargs: dict[str, Any] = {}
        if "trace_id" in q:
            kwargs["trace_id"] = q["trace_id"]
        if "limit" in q:
            try:
                kwargs["limit"] = int(q["limit"])
            except ValueError:
                kwargs["limit"] = 100
        try:
            entries = get_entries(**kwargs)
        except TypeError:
            # 시그니처가 다른 로거가 주입된 경우의 안전 폴백.
            entries = get_entries()

        # 추가 필터(level/module 등)는 응답 측에서 슬라이싱.
        level = q.get("level")
        module = q.get("module")
        out = []
        for e in entries:
            data = _entry_to_dict(e)
            if level and data.get("level") != level:
                continue
            if module and module not in (data.get("action_type") or ""):
                continue
            out.append(data)
        return _json_ok({"entries": out})

    async def _handle_health(self, request: web.Request) -> web.Response:
        snapshot: dict = {
            "status": "ok",
            "uptime_seconds": int(time.time() - self._started_at)
            if self._started_at
            else 0,
            "metrics": self._metrics.__dict__,
            "pending_changes": bool(_load_pending(_pending_changes_path(self._state_dir))),
        }
        if self._health_provider is not None:
            try:
                extra = self._health_provider() or {}
                if isinstance(extra, dict):
                    snapshot.update(extra)
            except Exception:  # noqa: BLE001
                logger.exception("health_provider failed")
        return _json_ok(snapshot)

    async def _handle_system_info(self, request: web.Request) -> web.Response:
        """진단 정보 — 버전·PID·uptime·DB 경로·디스크 사용량을 반환한다.

        UI(System 화면) 좌측 카드의 정적 데이터원이며, 헬스 폴링과 분리해 1회만
        조회한다. 외부 부수효과가 없는 read-only 핸들러로 별도 감사 로그를
        남기지 않는다.
        """
        # 버전 정보 — pyproject.toml의 단일 소스를 importlib.metadata로 조회.
        version = "unknown"
        try:
            from importlib.metadata import PackageNotFoundError, version as _pkg_version

            try:
                version = _pkg_version("simpleclaw")
            except PackageNotFoundError:
                version = "unknown"
        except Exception:  # noqa: BLE001
            pass

        # 빌드 해시 — 환경변수(SIMPLECLAW_BUILD_SHA)가 있으면 사용. 운영자가
        # 명시 주입하지 않으면 None으로 둔다(수동 git 호출은 의도적으로 회피).
        build_sha = os.environ.get("SIMPLECLAW_BUILD_SHA") or None

        # config.yaml에서 daemon.db_path를 우선 채택 — 없으면 admin_state_dir의
        # 형제 conversations.db를 폴백으로 노출(파일 존재 여부도 함께 응답).
        cfg = self._read_yaml()
        db_path_str = (
            _get_dotted(cfg, "agent.db_path")
            or _get_dotted(cfg, "daemon.db_path")
            or ".agent/conversations.db"
        )
        db_path = Path(str(db_path_str)).expanduser()
        db_size = None
        db_exists = db_path.is_file()
        if db_exists:
            try:
                db_size = db_path.stat().st_size
            except OSError:
                db_size = None

        # 디스크 사용량 — config.yaml이 있는 디렉토리(워크스페이스 루트로 간주)를
        # 기준으로 한 번만 측정. 컨테이너/원격 마운트에서는 데몬 위치가 더 의미 있다.
        disk = None
        try:
            target = self._config_path.parent if self._config_path.parent.exists() else Path.cwd()
            usage = shutil.disk_usage(target)
            disk = {
                "path": str(target),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            }
        except OSError:
            disk = None

        snapshot: dict[str, Any] = {
            "version": version,
            "build_sha": build_sha,
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "pid": os.getpid(),
            "uptime_seconds": int(time.time() - self._started_at)
            if self._started_at
            else 0,
            "config_path": str(self._config_path),
            "db_path": str(db_path),
            "db_exists": db_exists,
            "db_size_bytes": db_size,
            "disk": disk,
            "host": self._host,
            "port": self._port,
        }
        return _json_ok(snapshot)

    async def _handle_system_restart(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            body = {}
        if not isinstance(body, dict):
            body = {}

        # 펜딩 변경을 yaml에 머지 — 데몬이 재기동하면서 새 값을 읽도록.
        pending_path = _pending_changes_path(self._state_dir)
        pending = _load_pending(pending_path)
        merged_count = 0
        if pending:
            full = self._read_yaml()
            _deep_merge(full, pending)
            self._write_yaml(full)
            try:
                pending_path.unlink()
            except OSError:
                pass
            merged_count = sum(1 for _ in _flatten_keys(pending))

        entry = self._audit.append(
            action="system.restart",
            area="system",
            target="daemon",
            before=None,
            after={"reason": body.get("reason", ""), "applied_pending": merged_count},
            outcome="applied",
            requires_restart=True,
            undoable=False,
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )

        if self._restart_cb is not None:
            try:
                result = self._restart_cb(body)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[func-returns-value]
            except Exception:  # noqa: BLE001
                logger.exception("restart_callback failed")

        return _json_ok(
            {
                "outcome": "applied",
                "audit_id": entry.id,
                "applied_pending": merged_count,
            }
        )


# ---------------------------------------------------------------------------
# 모듈 헬퍼
# ---------------------------------------------------------------------------


def _json_ok(payload: dict) -> web.Response:
    return web.json_response(payload, status=200)


def _json_error(status: int, message: str, *, details: dict | None = None) -> web.Response:
    body: dict = {"error": message}
    if details:
        body.update(details)
    return web.json_response(body, status=status)


def _actor_from(request: web.Request) -> str:
    """현재 액터를 결정 — 단일 운영자 가정에서는 ``local``."""
    return request.headers.get("X-Actor-Id", "local")


def _truthy_query(request: web.Request, key: str) -> bool:
    """쿼리스트링에서 ``true``/``1``/``yes`` 등 진리값을 해석한다."""
    raw = request.query.get(key, "").lower()
    return raw in ("1", "true", "yes", "on")


def _project(full: dict, area: str, patch: dict) -> dict:
    """``patch``와 같은 형태로 ``full``에서 동일 키만 투영해 ``before`` 스냅샷을 만든다."""
    mapping = AREA_TO_YAML_KEY.get(area, area)
    if isinstance(mapping, list):
        out: dict = {}
        for key in mapping:
            if key in patch and isinstance(patch[key], dict):
                out[key] = _project_subtree(full.get(key, {}), patch[key])
        return out
    if isinstance(mapping, str) and "." in mapping:
        sub = _get_dotted(full, mapping) or {}
        return _project_subtree(sub, patch)
    sub = full.get(mapping, {}) if isinstance(mapping, str) else {}
    return _project_subtree(sub, patch)


def _project_subtree(source: object, shape: dict) -> dict:
    """``shape``의 키 구조와 같은 dict만 ``source``에서 추출한다."""
    out: dict = {}
    if not isinstance(source, dict):
        return out
    for k, v in shape.items():
        if k not in source:
            out[k] = None
            continue
        if isinstance(v, dict) and isinstance(source[k], dict):
            out[k] = _project_subtree(source[k], v)
        else:
            out[k] = copy.deepcopy(source[k])
    return out


def _policy_to_dict(p: PolicyResult) -> dict:
    return {
        "level": p.level,
        "requires_restart": p.requires_restart,
        "affected_modules": list(p.affected_modules),
        "matched_keys": list(p.matched_keys),
    }


def _audit_to_dict(e: AuditEntry) -> dict:
    return {
        "id": e.id,
        "ts": e.ts,
        "actor_id": e.actor_id,
        "trace_id": e.trace_id,
        "action": e.action,
        "area": e.area,
        "target": e.target,
        "before": e.before,
        "after": e.after,
        "outcome": e.outcome,
        "requires_restart": e.requires_restart,
        "affected_modules": list(e.affected_modules),
        "undoable": e.undoable,
        "reason": e.reason,
    }


def _entry_to_dict(e: object) -> dict:
    """``LogEntry`` 또는 dict를 통일된 dict로 직렬화한다."""
    if isinstance(e, dict):
        return e
    if hasattr(e, "to_dict"):
        try:
            return e.to_dict()  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001
            pass
    # dataclasses.asdict 폴백.
    try:
        from dataclasses import asdict
        return asdict(e)
    except Exception:  # noqa: BLE001
        return {"raw": str(e)}


def _list_backend_keys(backend: SecretBackend) -> list[str]:
    """백엔드별로 등록된 키 이름 목록을 반환한다.

    공식 인터페이스에는 ``list``가 없지만 운영상 메타데이터 노출이 필요하다.
    백엔드 구현에 ``list_keys()``가 있으면 사용하고, ``EncryptedFileBackend``는
    내부 볼트 파일을 직접 들여다본다(Fernet 토큰만 노출). 그 외에는 빈 리스트.
    """
    list_fn = getattr(backend, "list_keys", None)
    if callable(list_fn):
        try:
            return list(list_fn())
        except Exception:  # noqa: BLE001
            return []
    if isinstance(backend, EncryptedFileBackend):
        # vault 파일을 직접 읽어 이름만 노출 — 값 해독은 하지 않는다.
        try:
            data = backend._read_vault()  # noqa: SLF001 — 의도적 접근
            return list(data.keys())
        except SecretsError:
            return []
    return []


def _detect_backend(manager: SecretsManager, name: str) -> str | None:
    """``name``이 어느 백엔드에 존재하는지 탐지한다 — 회전 시 backend 미지정 폴백."""
    for label in _BACKEND_LABELS:
        try:
            backend = manager.get_backend(label)
        except SecretsError:
            continue
        try:
            if backend.get(name) is not None:
                return label
        except SecretsError:
            continue
    return None


def _last_rotated_for(audit: AuditLog, name: str) -> str | None:
    """주어진 시크릿 이름의 마지막 회전 시각을 감사 로그에서 찾는다."""
    entries = audit.search(action="secret.rotate", limit=1000)
    for e in reversed(entries):
        # target 형식: ``{backend}:{name}``
        if e.target.endswith(":" + name) or e.target == name:
            return e.ts
    return None


def _flatten_keys(d: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.extend(_flatten_keys(v, path))
        else:
            out.append(path)
    return out


def _rotate_master_key(backend: EncryptedFileBackend) -> int:
    """마스터 키 회전 + 모든 file 시크릿 재암호화. 재암호화한 항목 수를 반환.

    백업: 기존 마스터 키 파일은 ``master.key.{ts}.bak``으로 보존한다.
    환경변수 ``SIMPLECLAW_MASTER_KEY``를 우선 사용 중이라면 파일 백업은 건너뛰고
    회전 후 환경변수를 같은 값으로 갱신한다 — 운영자가 외부 비밀 저장소를
    그 후 직접 갱신해야 한다.
    """
    from cryptography.fernet import Fernet  # type: ignore[import-untyped]

    # 1) 모든 시크릿 평문화.
    plaintexts: dict[str, str] = {}
    data = backend._read_vault()  # noqa: SLF001
    for name in list(data.keys()):
        try:
            value = backend.get(name)
        except SecretsError:
            continue
        if value is not None:
            plaintexts[name] = value

    # 2) 새 마스터 키 생성 + 백업.
    import os
    from simpleclaw.security.secrets import MASTER_KEY_ENV

    new_key = Fernet.generate_key()
    env_overrides = os.environ.get(MASTER_KEY_ENV)
    key_path = backend._master_key_path  # noqa: SLF001
    if env_overrides is None and key_path.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = key_path.with_suffix(key_path.suffix + f".{ts}.bak")
        try:
            bak.write_bytes(key_path.read_bytes())
            try:
                bak.chmod(0o600)
            except OSError:
                pass
        except OSError:
            pass

    if env_overrides is not None:
        os.environ[MASTER_KEY_ENV] = new_key.decode("utf-8")
    else:
        # 파일 권한 0600으로 재기록.
        key_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(key_path, flags, 0o600)
        try:
            os.write(fd, new_key)
        finally:
            os.close(fd)

    # 3) 빈 볼트로 초기화 후 새 키로 다시 저장.
    backend._write_vault({})  # noqa: SLF001
    for name, value in plaintexts.items():
        backend.set(name, value)

    return len(plaintexts)


# ---------------------------------------------------------------------------
# 모듈 export
# ---------------------------------------------------------------------------

__all__ = [
    "AdminAPIServer",
    "AdminAPIMetrics",
    "AREA_TO_YAML_KEY",
    "_pending_changes_path",
]
