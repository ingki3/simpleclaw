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
import logging
import os
import secrets
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
    PolicyResult,
)
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming_runs import DreamingRunStore
from simpleclaw.memory.insights import InsightStore
from simpleclaw.memory.suggestions import (
    BlocklistStore,
    SuggestionStore,
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
    "admin_api": "admin_api",
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
# BIZ-245 — 시크릿 회전 후크: ``(backend, name, new_value)`` 를 받아 외부 동기화(예:
# ``web/admin/.env.local`` 의 ``ADMIN_API_TOKEN`` 갱신)를 수행한다. 회전 자체는
# 이미 성공한 상태에서 호출되므로 콜백 실패는 회전을 되돌리지 않으며 (로그만) — 새 토큰
# 값이 sink 에 도달하지 않아도 데몬 측 백엔드는 이미 갱신된 상태.
SecretRotationCallback = Callable[[str, str, str], None]
DashboardRouteRegistrar = Callable[[web.Application], object]


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
        study_status_service: object | None = None,
        secret_rotation_callback: SecretRotationCallback | None = None,
        dashboard_registrar: DashboardRouteRegistrar | None = None,
        dashboard_metrics: object | None = None,
        dashboard_structured_logger: object | None = None,
        dashboard_conversation_store: ConversationStore | None = None,
        dashboard_rag_log_window_days: int = 7,
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

        # BIZ-395 — Agent Study Wiki 관찰성. 명시 주입하지 않으면 study 라우트가
        # ``config.yaml`` 의 study.wiki_dir 로 store 를 지연 구성한다(부팅 경로에
        # study 의존성을 강제로 엮지 않기 위함). 미구성 wiki 는 configured=false 로 응답.
        self._study_status_service = study_status_service

        # BIZ-245 — 시크릿 회전 후 외부 동기화 후크 (예: ``web/admin/.env.local`` 의
        # ``ADMIN_API_TOKEN`` 갱신). vault 만 회전되고 Next 프록시가 옛 토큰으로 forward
        # 하는 사고(BIZ-244)를 재발 방지하려면 회전 시점에 sink 도 함께 갱신해야 한다.
        # 미주입 시 후크 자체가 비활성화되며 회전 응답에는 영향 없음.
        self._secret_rotation_cb = secret_rotation_callback

        # 8082 통합 대시보드 — registrar가 직접 주입되거나, metrics/logger가
        # 넘어오면 기본 DashboardServer adapter를 지연 import로 붙인다.
        self._dashboard_registrar = dashboard_registrar
        self._dashboard_metrics = dashboard_metrics
        self._dashboard_structured_logger = dashboard_structured_logger
        self._dashboard_conversation_store = dashboard_conversation_store
        self._dashboard_rag_log_window_days = dashboard_rag_log_window_days
        self._dashboard_routes_registered = False

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
        _admin_routes.register_admin_routes(app, self, prefix)
        self._register_dashboard_routes_if_configured(app)

        self._app = app
        return app

    def _register_dashboard_routes_if_configured(self, app: web.Application) -> None:
        """Admin API 앱에 기존 dashboard HTML/API 라우트를 함께 붙인다."""
        registrar = self._dashboard_registrar
        if registrar is None and (
            self._dashboard_metrics is not None
            and self._dashboard_structured_logger is not None
        ):
            from simpleclaw.logging.dashboard import register_dashboard_routes

            def registrar(target_app: web.Application) -> object:
                return register_dashboard_routes(
                    target_app,
                    metrics=self._dashboard_metrics,
                    structured_logger=self._dashboard_structured_logger,
                    conversation_store=self._dashboard_conversation_store,
                    rag_log_window_days=self._dashboard_rag_log_window_days,
                )

        if registrar is None:
            return
        registrar(app)
        self._dashboard_routes_registered = True

    @property
    def dashboard_routes_registered(self) -> bool:
        """대시보드 라우트가 Admin API 앱에 통합됐는지 반환한다."""
        return self._dashboard_routes_registered

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

    # Route handler implementations live in ``simpleclaw.channels.admin_routes``.
    # They are bound to this class at module import time to preserve the public
    # test/internal method surface while keeping this orchestration module small.

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

    # ------------------------------------------------------------------
    # 설정 — PATCH (dry-run / 적용 / pending)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 시크릿 — 메타데이터 / reveal / rotate / master rotate
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 감사 — 검색 / undo
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 로그 / 헬스 / 시스템
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 채널 테스트 발송 — telegram / webhook
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # BIZ-77 (F: Insight Source Linkage) — 인사이트 → 근거 메시지 역추적
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # BIZ-79 (H: Dreaming Dry-run + Admin Review Loop) — pending suggestion 큐
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # BIZ-92 — Insights 라우트(4-탭) 의 Active/Archive/Blocklist 데이터 공급
    # ------------------------------------------------------------------
    #
    # `/memory/insights` 페이지의 Review 탭은 이미 `_handle_list_suggestions`
    # 가 채우지만, 나머지 세 탭은 InsightStore / BlocklistStore 의 read-only
    # listing 이 필요하다. 두 listing 핸들러는 mutation 을 일으키지 않고
    # 기존 sidecar 데이터를 그대로 직렬화한다.

    # ------------------------------------------------------------------
    # BIZ-81 — 드리밍 운영 관측성 핸들러
    # ------------------------------------------------------------------


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


# Route modules import helpers from this module, so bind them only after every
# shared helper above is defined.
from simpleclaw.channels import admin_routes as _admin_routes  # noqa: E402

_admin_routes.bind_admin_route_handlers(AdminAPIServer)

