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
- ``POST   /admin/v1/channels/{name}/test``   — 채널 테스트 발송 (telegram/webhook)

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
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming_runs import DreamingRunStore
from simpleclaw.memory.insights import InsightStore
from simpleclaw.memory.suggestions import (
    BlocklistStore,
    SuggestionStore,
    TERMINAL_STATUSES,
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
    channel_tests: int = 0  # POST /admin/v1/channels/{name}/test 호출 수
    channel_tests_failed: int = 0  # 위 중 ok=False로 끝난 수


# 콜백 — 시스템 재시작 트리거. 호출자(run_bot.py 등)가 실제 재시작 메커니즘을 주입.
RestartCallback = Callable[[dict], "Awaitable[None] | None"]
ReloadCallback = Callable[[str, dict], "Awaitable[None] | None"]
# 채널 테스트 발송 콜백 — 호출자가 (channel_name, options) 받아 결과 dict를 반환.
# 결과는 최소 ``{ok: bool, status_code: int, latency_ms: int}`` 형태이며,
# 실패 시 ``error: str``을 추가한다. 미지정 시 admin_api 내부 기본 구현이 동작한다.
ChannelTestCallback = Callable[[str, dict], "Awaitable[dict] | dict"]


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
        channel_test_callback: ChannelTestCallback | None = None,
        cors_origins: list[str] | None = None,
        request_max_body_bytes: int | None = None,
        conversation_store: ConversationStore | None = None,
        insight_store: InsightStore | None = None,
        suggestion_store: SuggestionStore | None = None,
        blocklist_store: BlocklistStore | None = None,
        suggestion_writer: Callable[[str], None] | None = None,
        dreaming_run_store: DreamingRunStore | None = None,
        dreaming_status_provider: Callable[[], dict] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._config_path = Path(config_path)
        # CORS allowlist — 비어 있으면 헤더를 부착하지 않아 동일 origin만 허용한다.
        # 단일 운영자 가정의 dev 환경(예: Admin UI가 별도 포트에서 실행)을 위한 옵션.
        self._cors_origins = list(cors_origins or [])
        # 본문 크기 상한 — 설정 PATCH 페이로드는 일반적으로 작으므로 256 KiB 기본.
        # ``None``이면 제한 없음(테스트 편의).
        self._max_body_bytes = request_max_body_bytes

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
        self._channel_test_cb = channel_test_callback

        # BIZ-77 — 인사이트 source 역추적 엔드포인트가 사용하는 의존성. 둘 중
        # 하나라도 None 이면 ``/memory/insights/{id}/sources`` 가 503 으로 응답한다.
        # (테스트 편의/주입 안 한 환경에서 라우트는 등록하되 핸들러가 503 으로
        # disabled 사실을 명시적으로 알리는 편이 404 보다 운영 진단에 유리하다.)
        self._conversation_store = conversation_store
        self._insight_store = insight_store

        # BIZ-79 — pending suggestion 큐 + reject 블록리스트 + USER.md writer.
        # 셋이 모두 주입되어야 ``/memory/suggestions/...`` 라우트가 의미 있게 동작:
        # - suggestion_store 가 없으면 503 (큐 자체가 꺼짐)
        # - blocklist_store 가 없으면 reject 가 차단 효과를 못 냄 (503)
        # - suggestion_writer 가 없으면 accept/edit 에서 USER.md 가 안 갱신 (503)
        # 운영 환경(run_bot)에서는 셋 다 DreamingPipeline 으로부터 주입된다.
        self._suggestion_store = suggestion_store
        self._blocklist_store = blocklist_store
        self._suggestion_writer = suggestion_writer

        # BIZ-81 — 드리밍 메트릭 sidecar + 상태 프로바이더 (last_run, next_run, 운영 진단).
        # - dreaming_run_store: 사이클 단위 메트릭(JSONL). 미주입 시 503.
        # - dreaming_status_provider: 데몬에서 만든 상태 dict 를 반환하는 callable.
        #   포함 키: last_run(iso|None), next_run(iso|None), overnight_hour(int),
        #   idle_threshold(int), trigger_blockers(list[str]) 등 — 운영 진단 메시지.
        #   미주입 시 status 응답에 None 으로 비워둔다(엔드포인트 자체는 동작).
        self._dreaming_run_store = dreaming_run_store
        self._dreaming_status_provider = dreaming_status_provider

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

        # 본문 크리 상한이 지정되면 aiohttp 레벨에서 차단 — 핸들러 도달 전에 413 응답.
        kwargs: dict[str, Any] = {}
        if self._max_body_bytes is not None:
            kwargs["client_max_size"] = self._max_body_bytes
        app = web.Application(**kwargs)

        # CORS — 설정된 origin이 있을 때만 미들웨어를 등록한다. preflight(OPTIONS)는
        # 인증 래퍼를 거치지 않고 즉시 204를 반환해 Admin UI dev 서버가 정상 동작하도록.
        if self._cors_origins:
            app.middlewares.append(self._cors_middleware())
            app.router.add_route(
                "OPTIONS", "/admin/v1/{tail:.*}", self._handle_cors_preflight
            )

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
        app.router.add_post(
            f"{prefix}/channels/{{name}}/test",
            self._wrap(self._handle_test_channel),
        )

        # BIZ-77 (F: Insight Source Linkage) — 인사이트 → 근거 메시지 역추적.
        # 토픽 키(원문 또는 정규형)를 path 로 받는다. 한국어 토픽은 URL 인코딩되어
        # 들어와도 aiohttp 가 자동 디코딩한다.
        app.router.add_get(
            f"{prefix}/memory/insights/{{topic}}/sources",
            self._wrap(self._handle_get_insight_sources),
        )

        # BIZ-92 — `/memory/insights` 4-탭 페이지의 Active/Archive/Blocklist
        # 데이터 공급. read-only listing.
        app.router.add_get(
            f"{prefix}/memory/insights",
            self._wrap(self._handle_list_insights),
        )
        app.router.add_get(
            f"{prefix}/memory/blocklist",
            self._wrap(self._handle_list_blocklist),
        )

        # BIZ-79 (H: Dreaming Dry-run + Admin Review Loop) — 큐 운영 엔드포인트.
        # GET    /memory/suggestions               — pending 목록
        # GET    /memory/suggestions/{id}/sources  — 근거 메시지 (BIZ-77 재사용)
        # POST   /memory/suggestions/{id}/accept   — 원문 그대로 USER.md 적용
        # POST   /memory/suggestions/{id}/edit     — body.text 로 치환해 적용
        # POST   /memory/suggestions/{id}/reject   — 블록리스트 추가 + 큐에서 제거
        app.router.add_get(
            f"{prefix}/memory/suggestions",
            self._wrap(self._handle_list_suggestions),
        )
        app.router.add_get(
            f"{prefix}/memory/suggestions/{{sid}}/sources",
            self._wrap(self._handle_get_suggestion_sources),
        )
        app.router.add_post(
            f"{prefix}/memory/suggestions/{{sid}}/accept",
            self._wrap(self._handle_accept_suggestion),
        )
        app.router.add_post(
            f"{prefix}/memory/suggestions/{{sid}}/edit",
            self._wrap(self._handle_edit_suggestion),
        )
        app.router.add_post(
            f"{prefix}/memory/suggestions/{{sid}}/reject",
            self._wrap(self._handle_reject_suggestion),
        )

        # BIZ-81 (K: Dreaming 운영 관측성) — 사이클 메트릭 + 상태 진단.
        # GET /memory/dreaming/runs?limit=N  — 최근 N건 회차 메트릭 (기본 20).
        # GET /memory/dreaming/status         — last_run / next_run + 7일 KPI + 진단 메시지.
        app.router.add_get(
            f"{prefix}/memory/dreaming/runs",
            self._wrap(self._handle_list_dreaming_runs),
        )
        app.router.add_get(
            f"{prefix}/memory/dreaming/status",
            self._wrap(self._handle_dreaming_status),
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
    # CORS — Admin UI dev 서버(별도 origin)에서의 호출을 허용하기 위한 최소 구현.
    # ------------------------------------------------------------------

    def _cors_middleware(self):
        """허용 origin과 일치하면 CORS 응답 헤더를 부착하는 aiohttp 미들웨어."""

        @web.middleware
        async def middleware(request: web.Request, handler):
            response = await handler(request)
            origin = request.headers.get("Origin", "")
            if origin and origin in self._cors_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin"
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        return middleware

    async def _handle_cors_preflight(self, request: web.Request) -> web.Response:
        """OPTIONS preflight — 인증 없이 허용 origin에만 204를 반환한다."""
        origin = request.headers.get("Origin", "")
        if not origin or origin not in self._cors_origins:
            return _json_error(403, "Origin not allowed")
        req_method = request.headers.get("Access-Control-Request-Method", "GET")
        req_headers = request.headers.get(
            "Access-Control-Request-Headers", "Authorization, Content-Type"
        )
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": req_method,
                "Access-Control-Allow-Headers": req_headers,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            },
        )

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

    # ------------------------------------------------------------------
    # 채널 테스트 발송 — telegram / webhook
    # ------------------------------------------------------------------

    async def _handle_test_channel(self, request: web.Request) -> web.Response:
        """채널별 테스트 메시지를 발송하고 상태 코드/지연을 반환한다.

        - 경로 ``/admin/v1/channels/{name}/test``의 ``name``은 ``telegram``/``webhook``.
        - 요청 본문(JSON, 선택): ``{"message": "...", "target": "..."}``. 미지정 시
          ``"Hello from admin"`` + 채널 기본 타깃(텔레그램은 첫 화이트리스트 user_id,
          웹훅은 ``http://{host}:{port}/webhook``).
        - 응답: ``{ok, status_code, latency_ms, target?, error?}``.

        ``channel_test_callback``이 주입돼 있으면 위임하고, 그렇지 않으면 내장
        구현이 aiohttp.ClientSession으로 실제 호출을 수행한다 — 단위 테스트는
        콜백을 mock으로 주입해 외부 네트워크 의존을 끊는다.
        """
        name = request.match_info["name"]
        if name not in ("telegram", "webhook"):
            return _json_error(404, f"Unknown channel: {name}")

        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            body = {}
        if not isinstance(body, dict):
            body = {}
        message = (
            body.get("message")
            if isinstance(body.get("message"), str) and body.get("message")
            else "Hello from admin"
        )
        target_override = body.get("target")
        options: dict = {"message": message}
        if target_override is not None:
            options["target"] = target_override

        # 콜백 우선 — 호출자가 실제 송신 메커니즘을 주입한 경우.
        try:
            if self._channel_test_cb is not None:
                raw = self._channel_test_cb(name, options)
                if hasattr(raw, "__await__"):
                    result = await raw  # type: ignore[func-returns-value]
                else:
                    result = raw  # type: ignore[assignment]
            else:
                result = await self._default_channel_test(name, options)
        except Exception as exc:  # noqa: BLE001
            logger.exception("channel test failed: name=%s", name)
            result = {
                "ok": False,
                "status_code": 0,
                "latency_ms": 0,
                "error": f"테스트 발송 중 예외: {exc}",
            }

        if not isinstance(result, dict):
            result = {"ok": False, "status_code": 0, "latency_ms": 0, "error": "콜백 응답 형식 오류"}
        # 필수 필드 보강.
        result.setdefault("ok", False)
        result.setdefault("status_code", 0)
        result.setdefault("latency_ms", 0)

        self._metrics.channel_tests += 1
        if not result.get("ok"):
            self._metrics.channel_tests_failed += 1

        # 메시지 본문은 시크릿이 아니지만, target이 토큰을 포함할 수 있으므로
        # ``after``에는 마스킹 헬퍼를 한 번 통과시킨다.
        entry = self._audit.append(
            action="channel.test",
            area="channels",
            target=name,
            before=None,
            after=_mask_for_response(
                {
                    "message": message,
                    "target": result.get("target"),
                    "ok": result.get("ok"),
                    "status_code": result.get("status_code"),
                    "latency_ms": result.get("latency_ms"),
                }
            ),
            outcome="applied" if result.get("ok") else "rejected",
            requires_restart=False,
            undoable=False,
            reason=result.get("error") or "",
            actor_id=_actor_from(request),
            trace_id=request.headers.get("X-Trace-Id", ""),
        )
        return _json_ok({**result, "audit_id": entry.id})

    async def _default_channel_test(
        self, name: str, options: dict
    ) -> dict:
        """콜백 미주입 시의 내장 발송 구현 — aiohttp로 직접 호출한다.

        외부 네트워크에 닿으므로 격리된 단위 테스트는 ``channel_test_callback``을
        주입해 본 메서드를 우회한다.
        """
        import aiohttp  # 지연 임포트

        full_cfg = self._read_yaml()
        message = options.get("message") or "Hello from admin"

        if name == "telegram":
            tg = full_cfg.get("telegram") or {}
            token_ref = tg.get("bot_token")
            token = self._secrets.resolve(token_ref) if token_ref else ""
            if not token:
                return {
                    "ok": False,
                    "status_code": 0,
                    "latency_ms": 0,
                    "error": "telegram.bot_token이 설정되지 않았어요.",
                }
            target = options.get("target")
            if target is None:
                whitelist = tg.get("whitelist") or {}
                ids = (whitelist.get("user_ids") or []) + (
                    whitelist.get("chat_ids") or []
                )
                if not ids:
                    return {
                        "ok": False,
                        "status_code": 0,
                        "latency_ms": 0,
                        "error": "telegram whitelist가 비어 있어 발송 대상이 없어요.",
                    }
                target = ids[0]

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": target, "text": message}
            return await _http_test_send(url, payload, target=str(target))

        # webhook — 자체 수신 엔드포인트로 POST.
        wh = full_cfg.get("webhook") or {}
        host = wh.get("host", "127.0.0.1")
        port = wh.get("port", 8080)
        auth_ref = wh.get("auth_token")
        auth_token = self._secrets.resolve(auth_ref) if auth_ref else ""
        target = options.get("target") or f"http://{host}:{port}/webhook"
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        # 웹훅 페이로드 모양은 ``WebhookEvent`` 직렬화에 맞춰 최소 필드만.
        payload = {
            "action_type": "test",
            "message": message,
            "source": "admin-ui",
        }
        return await _http_test_send(
            str(target), payload, target=str(target), headers=headers
        )

    # ------------------------------------------------------------------
    # BIZ-77 (F: Insight Source Linkage) — 인사이트 → 근거 메시지 역추적
    # ------------------------------------------------------------------

    async def _handle_get_insight_sources(
        self, request: web.Request
    ) -> web.Response:
        """``GET /admin/v1/memory/insights/{topic}/sources``.

        주어진 topic (원문 또는 정규형) 의 인사이트 메타를 sidecar 에서 찾고,
        ``source_msg_ids`` 가 가리키는 메시지를 ``ConversationStore`` 에서 조회해
        Admin UI 에 노출할 형태로 반환한다.

        실패 응답:
        - 503: ``conversation_store`` 또는 ``insight_store`` 가 주입되지 않음
          (Admin API 가 메모리 스택 없이 부팅된 환경 — silent 404 보다 명시적인
          503 이 운영 진단에 유리하다).
        - 404: 해당 topic 의 인사이트가 sidecar 에 없음.
        - 422: topic path 가 비어 있거나 정규화 후 빈 문자열.
        """
        if self._conversation_store is None or self._insight_store is None:
            return _json_error(
                503,
                "Insight source linkage is not configured on this server",
            )

        # match_info 는 aiohttp 가 URL 디코딩한 값을 돌려준다. 양 끝 공백 트림.
        topic_param = (request.match_info.get("topic") or "").strip()
        if not topic_param:
            return _json_error(422, "topic path parameter is required")

        meta = self._insight_store.find_by_topic(topic_param)
        if meta is None:
            return _json_error(404, f"Insight not found for topic: {topic_param}")

        # source 메시지가 없으면 빈 배열을 반환 — UI 에서 "근거 메시지 없음" 처리.
        rows = self._conversation_store.get_messages_by_ids(meta.source_msg_ids)
        sources = [
            {
                "id": mid,
                "role": msg.role.value,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "channel": msg.channel,
                "token_count": msg.token_count,
            }
            for mid, msg in rows
        ]

        return _json_ok({
            "topic": meta.topic,
            "text": meta.text,
            "evidence_count": meta.evidence_count,
            "confidence": meta.confidence,
            "first_seen": meta.first_seen.isoformat(),
            "last_seen": meta.last_seen.isoformat(),
            "start_msg_id": meta.start_msg_id,
            "end_msg_id": meta.end_msg_id,
            "source_msg_ids": list(meta.source_msg_ids),
            "sources": sources,
        })

    # ------------------------------------------------------------------
    # BIZ-79 (H: Dreaming Dry-run + Admin Review Loop) — pending suggestion 큐
    # ------------------------------------------------------------------

    def _suggestions_disabled_response(self) -> web.Response | None:
        """``suggestion_store`` 미주입 환경에서 503 응답을 만든다.

        accept/edit 는 ``suggestion_writer`` 도, reject 는 ``blocklist_store`` 도
        함께 필요한데 그 디테일은 각 핸들러가 별도로 검사한다. 본 헬퍼는 모든
        엔드포인트가 공통으로 거치는 1차 가드.
        """
        if self._suggestion_store is None:
            return _json_error(
                503,
                "Suggestion queue is not configured on this server",
            )
        return None

    def _serialize_suggestion(self, s) -> dict:
        """``SuggestionMeta`` 를 JSON 응답 dict 로 변환."""
        return {
            "id": s.id,
            "topic": s.topic,
            "text": s.text,
            "edited_text": s.edited_text,
            "applied_text": s.applied_text,
            "confidence": s.confidence,
            "evidence_count": s.evidence_count,
            "source_msg_ids": list(s.source_msg_ids),
            "start_msg_id": s.start_msg_id,
            "end_msg_id": s.end_msg_id,
            "status": s.status,
            "reject_reason": s.reject_reason,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
        }

    async def _handle_list_suggestions(
        self, request: web.Request
    ) -> web.Response:
        """``GET /admin/v1/memory/suggestions``.

        Query params:
        - ``status``: ``pending`` (default) / ``all`` / ``accepted`` / ``edited`` / ``rejected``.
          ``all`` 은 디버깅/감사 용도로 history 전체를 노출한다.

        Response:
        ``{"suggestions": [...], "total": N, "pending_count": M, "auto_promote": {...}}``
        """
        guard = self._suggestions_disabled_response()
        if guard is not None:
            return guard

        status_filter = (request.query.get("status") or "pending").strip().lower()
        all_items = self._suggestion_store.load()
        if status_filter == "all":
            items = list(all_items)
        else:
            items = [s for s in all_items if s.status == status_filter]
        items.sort(key=lambda s: s.updated_at, reverse=True)

        return _json_ok({
            "suggestions": [self._serialize_suggestion(s) for s in items],
            "total": len(items),
            "pending_count": sum(1 for s in all_items if s.status == "pending"),
        })

    async def _handle_get_suggestion_sources(
        self, request: web.Request
    ) -> web.Response:
        """``GET /admin/v1/memory/suggestions/{sid}/sources``.

        BIZ-79 DoD §3 — UI 의 "근거 메시지 보기" 액션이 호출하는 엔드포인트.
        ``ConversationStore`` 미주입이면 503, suggestion 미존재면 404.
        """
        guard = self._suggestions_disabled_response()
        if guard is not None:
            return guard
        if self._conversation_store is None:
            return _json_error(
                503, "Source linkage is not configured on this server"
            )

        sid = (request.match_info.get("sid") or "").strip()
        s = self._suggestion_store.get(sid)
        if s is None:
            return _json_error(404, f"Suggestion not found: {sid}")

        rows = self._conversation_store.get_messages_by_ids(s.source_msg_ids)
        sources = [
            {
                "id": mid,
                "role": msg.role.value,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "channel": msg.channel,
                "token_count": msg.token_count,
            }
            for mid, msg in rows
        ]
        return _json_ok({
            "suggestion": self._serialize_suggestion(s),
            "sources": sources,
        })

    @staticmethod
    async def _read_json_body(request: web.Request) -> dict:
        """JSON 본문을 안전하게 dict 로 읽는다 (없거나 잘못되면 빈 dict)."""
        try:
            raw = await request.read()
        except Exception:  # noqa: BLE001
            return {}
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _audit_suggestion(
        self,
        request: web.Request,
        action: str,
        suggestion,
        details: dict | None = None,
    ) -> None:
        """Suggestion mutation 을 감사 로그에 기록한다.

        operator review action 은 USER.md 변경/블록리스트 추가로 이어지므로
        ``rotate``/``patch_config`` 와 동일한 수준의 감사 추적이 필요하다.
        """
        try:
            after: dict = {
                "id": suggestion.id,
                "topic": suggestion.topic,
                "status": suggestion.status,
            }
            if details:
                after.update(details)
            self._audit.append(
                action=action,
                area="memory",
                target=f"suggestion:{suggestion.id}",
                before={"topic": suggestion.topic, "text": suggestion.text},
                after=after,
                actor_id=_actor_from(request),
                undoable=False,  # USER.md append/blocklist add 는 단방향 액션
            )
        except Exception:  # noqa: BLE001 — 감사 실패가 핸들러 응답을 막지 않도록.
            logger.exception("Failed to write suggestion audit entry")

    async def _handle_accept_suggestion(
        self, request: web.Request
    ) -> web.Response:
        """``POST /admin/v1/memory/suggestions/{sid}/accept`` — 원문 그대로 적용."""
        guard = self._suggestions_disabled_response()
        if guard is not None:
            return guard
        if self._suggestion_writer is None:
            return _json_error(
                503,
                "USER.md writer is not configured — cannot apply suggestions",
            )

        sid = (request.match_info.get("sid") or "").strip()
        s = self._suggestion_store.get(sid)
        if s is None:
            return _json_error(404, f"Suggestion not found: {sid}")
        if s.status in TERMINAL_STATUSES:
            return _json_error(
                409,
                f"Suggestion already in terminal state: {s.status}",
            )

        try:
            self._suggestion_writer(s.text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to write accepted suggestion")
            return _json_error(500, f"Failed to apply suggestion: {exc}")

        updated = self._suggestion_store.update_status(sid, "accepted")
        # update_status 의 반환값이 None 이라면 race — 그래도 writer 가 이미 USER.md
        # 를 갱신했으므로 200 으로 응답하고 클라이언트에 최선 정보 제공.
        result = updated or s
        self._audit_suggestion(request, "accept_suggestion", result)
        return _json_ok(self._serialize_suggestion(result))

    async def _handle_edit_suggestion(
        self, request: web.Request
    ) -> web.Response:
        """``POST /admin/v1/memory/suggestions/{sid}/edit`` — body.text 로 치환 후 적용."""
        guard = self._suggestions_disabled_response()
        if guard is not None:
            return guard
        if self._suggestion_writer is None:
            return _json_error(
                503,
                "USER.md writer is not configured — cannot apply suggestions",
            )

        sid = (request.match_info.get("sid") or "").strip()
        body = await self._read_json_body(request)
        edited_text = (body.get("text") or "").strip()
        if not edited_text:
            return _json_error(422, "Body must include non-empty 'text' field")

        s = self._suggestion_store.get(sid)
        if s is None:
            return _json_error(404, f"Suggestion not found: {sid}")
        if s.status in TERMINAL_STATUSES:
            return _json_error(
                409,
                f"Suggestion already in terminal state: {s.status}",
            )

        try:
            self._suggestion_writer(edited_text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to write edited suggestion")
            return _json_error(500, f"Failed to apply suggestion: {exc}")

        updated = self._suggestion_store.update_status(
            sid, "edited", edited_text=edited_text
        )
        result = updated or s
        self._audit_suggestion(
            request,
            "edit_suggestion",
            result,
            details={"edited_text": edited_text},
        )
        return _json_ok(self._serialize_suggestion(result))

    async def _handle_reject_suggestion(
        self, request: web.Request
    ) -> web.Response:
        """``POST /admin/v1/memory/suggestions/{sid}/reject`` — 블록리스트 추가."""
        guard = self._suggestions_disabled_response()
        if guard is not None:
            return guard
        if self._blocklist_store is None:
            return _json_error(
                503,
                "Blocklist store is not configured — cannot block topics",
            )

        sid = (request.match_info.get("sid") or "").strip()
        body = await self._read_json_body(request)
        reason = (body.get("reason") or "").strip() or None

        s = self._suggestion_store.get(sid)
        if s is None:
            return _json_error(404, f"Suggestion not found: {sid}")
        if s.status in TERMINAL_STATUSES:
            return _json_error(
                409,
                f"Suggestion already in terminal state: {s.status}",
            )

        # 1) 블록리스트 추가 — 다음 dreaming 사이클부터 같은 topic 은 필터링됨.
        # 2) suggestion 행 status 를 rejected 로 마킹 (UI 에서 사라짐, audit 보존).
        self._blocklist_store.add(s.topic, reason=reason)
        updated = self._suggestion_store.update_status(
            sid, "rejected", reject_reason=reason
        )
        result = updated or s
        self._audit_suggestion(
            request,
            "reject_suggestion",
            result,
            details={"reason": reason or ""},
        )
        return _json_ok(self._serialize_suggestion(result))

    # ------------------------------------------------------------------
    # BIZ-92 — Insights 라우트(4-탭) 의 Active/Archive/Blocklist 데이터 공급
    # ------------------------------------------------------------------
    #
    # `/memory/insights` 페이지의 Review 탭은 이미 `_handle_list_suggestions`
    # 가 채우지만, 나머지 세 탭은 InsightStore / BlocklistStore 의 read-only
    # listing 이 필요하다. 두 listing 핸들러는 mutation 을 일으키지 않고
    # 기존 sidecar 데이터를 그대로 직렬화한다.

    def _serialize_insight(self, meta) -> dict:
        """``InsightMeta`` 를 JSON 응답 dict 로 변환.

        Active/Archive 탭에서 InsightCard 가 직접 소비하는 필드 집합:
        topic/text/confidence/evidence/last_seen/start_msg_id/end_msg_id/
        archived_at. last_seen 은 카드의 메타 라인, archived_at 은 탭 분리에
        쓰인다.
        """
        return {
            "topic": meta.topic,
            "text": meta.text,
            "evidence_count": meta.evidence_count,
            "confidence": meta.confidence,
            "first_seen": meta.first_seen.isoformat(),
            "last_seen": meta.last_seen.isoformat(),
            "start_msg_id": meta.start_msg_id,
            "end_msg_id": meta.end_msg_id,
            "source_msg_ids": list(meta.source_msg_ids),
            "archived_at": (
                meta.archived_at.isoformat() if meta.archived_at else None
            ),
        }

    async def _handle_list_insights(self, request: web.Request) -> web.Response:
        """``GET /admin/v1/memory/insights``.

        Query params:
        - ``status``: ``active`` (default — archived_at is None) /
          ``archived`` (archived_at is not None) / ``all`` (debugging).

        Response:
        ``{"insights": [...], "total": N, "active_count": A, "archived_count": B}``

        503 if ``insight_store`` 가 주입되지 않음 — Admin API 가 메모리 스택
        없이 부팅된 환경 (silent 404 보다 명시적인 503 이 운영 진단에 유리).
        """
        if self._insight_store is None:
            return _json_error(
                503, "Insight store is not configured on this server"
            )

        status_filter = (request.query.get("status") or "active").strip().lower()
        all_items = list(self._insight_store.load().values())
        # 정렬: last_seen 내림차순 — 최근 관측이 위로. 운영자 멘탈 모델상 가장
        # 자연스럽다 (Active 는 새로 강화된 항목 우선, Archive 는 가장 최근에
        # 잠든 항목 우선).
        all_items.sort(key=lambda m: m.last_seen, reverse=True)
        if status_filter == "all":
            items = all_items
        elif status_filter == "archived":
            items = [m for m in all_items if m.is_archived()]
        else:  # default 'active'
            items = [m for m in all_items if not m.is_archived()]

        return _json_ok({
            "insights": [self._serialize_insight(m) for m in items],
            "total": len(items),
            "active_count": sum(1 for m in all_items if not m.is_archived()),
            "archived_count": sum(1 for m in all_items if m.is_archived()),
        })

    async def _handle_list_blocklist(self, request: web.Request) -> web.Response:
        """``GET /admin/v1/memory/blocklist``.

        BIZ-79 의 ``BlocklistStore`` 는 정규형 topic 한 줄당 ``{topic,
        topic_key, reason, blocked_at}`` 를 저장한다. 응답 형태:

        ``{"entries": [{topic, topic_key, reason, blocked_at}, ...],
           "total": N}``

        503 if ``blocklist_store`` 가 주입되지 않음. 정렬은 ``blocked_at``
        내림차순 — 운영자가 가장 최근 결정의 컨텍스트를 빠르게 회수.
        """
        if self._blocklist_store is None:
            return _json_error(
                503, "Blocklist store is not configured on this server"
            )

        entries = list(self._blocklist_store.load().values())
        # blocked_at 은 ISO 문자열 — string 비교가 ISO 형식에서 시간순 정렬과
        # 일치한다. 누락된 필드는 빈 문자열로 폴백해 정렬이 깨지지 않도록.
        entries.sort(key=lambda e: e.get("blocked_at") or "", reverse=True)
        return _json_ok({
            "entries": [
                {
                    "topic": e.get("topic", ""),
                    "topic_key": e.get("topic_key", ""),
                    "reason": e.get("reason", ""),
                    "blocked_at": e.get("blocked_at"),
                }
                for e in entries
            ],
            "total": len(entries),
        })

    # ------------------------------------------------------------------
    # BIZ-81 — 드리밍 운영 관측성 핸들러
    # ------------------------------------------------------------------

    def _serialize_dreaming_run(self, rec) -> dict:
        """``DreamingRunRecord`` 를 JSON 응답 dict 로 변환.

        UI 가 한 행에서 status/duration 을 즉시 표시할 수 있도록 파생 필드도 포함한다.
        """
        return {
            "id": rec.id,
            "started_at": rec.started_at.isoformat(),
            "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
            "duration_seconds": rec.duration_seconds,
            "input_msg_count": rec.input_msg_count,
            "generated_insight_count": rec.generated_insight_count,
            "rejected_count": rec.rejected_count,
            "error": rec.error,
            "skip_reason": rec.skip_reason,
            "status": rec.status,
            "details": rec.details or {},
        }

    def _suggestion_rejection_rate(self) -> dict:
        """``SuggestionStore`` 에 누적된 운영자 review 결과로부터 거절률 계산.

        DoD 의 "거절률" KPI 는 dreaming 결과(=suggestion) 에 대한 운영자 리뷰 신호이다
        (BIZ-66 §3-K: "거절률 KPI는 H의 Admin Review Loop 신호에서 산출"). 따라서
        suggestion_store 가 비활성이면 ``None`` 을 반환해 UI 가 "측정 불가" 로 표시.

        Returns:
            ``{"reviewed": int, "rejected": int, "rate": float|None}``.
            ``reviewed`` 가 0 이면 ``rate`` 는 ``None`` (분모 0).
        """
        if self._suggestion_store is None:
            return {"reviewed": 0, "rejected": 0, "rate": None}
        reviewed = 0
        rejected = 0
        for s in self._suggestion_store.load():
            if s.status in TERMINAL_STATUSES:
                reviewed += 1
                if s.status == "rejected":
                    rejected += 1
        rate = (rejected / reviewed) if reviewed > 0 else None
        return {"reviewed": reviewed, "rejected": rejected, "rate": rate}

    async def _handle_list_dreaming_runs(
        self, request: web.Request
    ) -> web.Response:
        """``GET /admin/v1/memory/dreaming/runs?limit=N``.

        최근 N건의 사이클 메트릭을 최신순으로 반환. ``limit`` 기본 20, 상한 200
        (sidecar 자체가 200건 정도만 보존).

        Response: ``{"runs": [...], "total": N}``.
        503 if ``dreaming_run_store`` 미주입.
        """
        if self._dreaming_run_store is None:
            return _json_error(
                503, "Dreaming run metrics are not configured on this server"
            )
        # limit 파라미터 — 정수가 아니면 기본값으로 폴백(검색바 오타 방어).
        try:
            limit = int(request.query.get("limit", "20"))
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(200, limit))

        rows = self._dreaming_run_store.list_recent(limit=limit)
        return _json_ok({
            "runs": [self._serialize_dreaming_run(r) for r in rows],
            "total": len(rows),
        })

    async def _handle_dreaming_status(
        self, request: web.Request
    ) -> web.Response:
        """``GET /admin/v1/memory/dreaming/status``.

        Memory 화면의 KPI 패널이 단일 호출로 받아갈 수 있도록 다음을 합쳐 반환:
        - ``last_run`` / ``last_successful_run``: 가장 최근 회차/성공 회차.
        - ``next_run``: 데몬에서 추정한 다음 실행 예정 시각(string ISO|None).
        - ``trigger`` 진단: overnight_hour, idle_threshold, blockers 등 오늘 트리거가
          왜 (아직) 안 돌았는지 사람이 읽을 수 있는 메시지.
        - ``kpi_7d``: 7일 윈도우 집계(success/skip/error 카운트, msg/insight totals,
          skip_breakdown).
        - ``rejection``: 운영자 리뷰 누적 거절률 (suggestion_store 에서 계산).

        ``dreaming_run_store`` 가 없으면 KPI 와 last_run 을 None 으로 비우되 응답
        자체는 200 으로 돌려준다 — UI 가 "메트릭 비활성" 안내를 그릴 수 있게.
        """
        last_run = None
        last_successful = None
        kpi_7d: dict | None = None
        if self._dreaming_run_store is not None:
            lr = self._dreaming_run_store.last_run()
            ls = self._dreaming_run_store.last_successful_run()
            if lr is not None:
                last_run = self._serialize_dreaming_run(lr)
            if ls is not None:
                last_successful = self._serialize_dreaming_run(ls)
            kpi_7d = self._dreaming_run_store.kpi_window(days=7)

        # 데몬에서 만든 status 컨텍스트 — 미주입 시 빈 dict 로 폴백(엔드포인트는 동작).
        provider_state: dict = {}
        if self._dreaming_status_provider is not None:
            try:
                provider_state = dict(self._dreaming_status_provider() or {})
            except Exception:
                # provider 실패는 KPI 응답을 막지 않는다 — 진단 가시성이 핵심.
                logger.exception("dreaming_status_provider raised; returning empty state")
                provider_state = {}

        return _json_ok({
            "last_run": last_run,
            "last_successful_run": last_successful,
            "next_run": provider_state.get("next_run"),
            "overnight_hour": provider_state.get("overnight_hour"),
            "idle_threshold_seconds": provider_state.get("idle_threshold_seconds"),
            "trigger_blockers": list(provider_state.get("trigger_blockers") or []),
            "trigger_message": provider_state.get("trigger_message"),
            "kpi_7d": kpi_7d,
            "rejection": self._suggestion_rejection_rate(),
            "metrics_enabled": self._dreaming_run_store is not None,
        })

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


async def _http_test_send(
    url: str,
    payload: dict,
    *,
    target: str,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 5.0,
) -> dict:
    """단일 POST 요청으로 테스트 메시지를 보내고 ok/status/latency를 측정한다.

    네트워크 실패는 ``ok=False`` + ``error``로 변환해 호출자에게 토스트 띄우기
    좋은 형태로 정규화한다.
    """
    import aiohttp  # 지연 임포트

    started = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers or {}) as resp:
                status = resp.status
                # 응답 본문은 디버깅 단서로만 짧게 보존.
                try:
                    text = await resp.text()
                except Exception:  # noqa: BLE001
                    text = ""
                latency_ms = int((time.monotonic() - started) * 1000)
                return {
                    "ok": 200 <= status < 300,
                    "status_code": status,
                    "latency_ms": latency_ms,
                    "target": target,
                    "response_preview": text[:200] if text else "",
                }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": latency_ms,
            "target": target,
            "error": str(exc),
        }


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
    "ChannelTestCallback",
    "_pending_changes_path",
]
