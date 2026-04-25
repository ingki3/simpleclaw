"""웹훅 서버: aiohttp 기반 경량 REST 엔드포인트.

외부 시스템으로부터 이벤트를 수신하는 HTTP 서버를 제공한다.
- POST /webhook : Bearer 토큰 인증 → 이벤트 파싱 → 핸들러 디스패치
- GET /health   : 서버 상태 확인용 헬스체크
- 인증 실패·성공 모두 AccessAttempt으로 기록하여 감사 추적 가능
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from aiohttp import web

from simpleclaw.channels.models import (
    AccessAttempt,
    EventActionType,
    WebhookEvent,
    WebhookError,
)

logger = logging.getLogger(__name__)


class WebhookServer:
    """aiohttp 기반 경량 HTTP 웹훅 수신 서버.

    Bearer 토큰 인증, JSON 페이로드 파싱, 이벤트 핸들러 디스패치를 담당한다.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        auth_token: str = "",
        event_handler=None,
    ) -> None:
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._event_handler = event_handler
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._running = False
        self._events: list[WebhookEvent] = []
        self._access_log: list[AccessAttempt] = []

    async def start(self) -> None:
        """웹훅 HTTP 서버를 시작한다."""
        self._app = web.Application()
        self._app.router.add_post("/webhook", self._handle_webhook)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        self._running = True
        logger.info("Webhook server started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """웹훅 HTTP 서버를 중지한다."""
        if self._runner:
            await self._runner.cleanup()
        self._running = False
        logger.info("Webhook server stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_events(self) -> list[WebhookEvent]:
        """수신된 이벤트 목록의 복사본을 반환한다."""
        return list(self._events)

    def get_access_log(self) -> list[AccessAttempt]:
        """접근 시도 로그의 복사본을 반환한다."""
        return list(self._access_log)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """헬스체크 엔드포인트 — 서버 생존 확인용."""
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """수신된 웹훅 POST 요청을 처리한다.

        1. Bearer 토큰 인증 검사
        2. JSON 페이로드 파싱 및 필수 필드 검증
        3. WebhookEvent 생성 후 이벤트 핸들러 호출
        """
        # 인증 검사
        if self._auth_token:
            auth_header = request.headers.get("Authorization", "")
            expected = f"Bearer {self._auth_token}"
            if auth_header != expected:
                self._access_log.append(AccessAttempt(
                    source="webhook",
                    user_identifier=request.remote or "unknown",
                    authorized=False,
                    details="Invalid or missing auth token",
                ))
                logger.warning(
                    "Webhook unauthorized access from %s", request.remote
                )
                return web.json_response(
                    {"error": "Unauthorized"}, status=401
                )

        self._access_log.append(AccessAttempt(
            source="webhook",
            user_identifier=request.remote or "unknown",
            authorized=True,
        ))

        # 요청 본문 파싱
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Invalid JSON payload"}, status=400
            )

        if not isinstance(body, dict):
            return web.json_response(
                {"error": "Payload must be a JSON object"}, status=400
            )

        # 필수 필드 검증
        event_type = body.get("event_type")
        if not event_type:
            return web.json_response(
                {"error": "Missing required field: event_type"}, status=400
            )

        # 액션 타입 파싱
        action_type = None
        action_ref = body.get("action_reference", "")
        raw_action = body.get("action_type")
        if raw_action:
            try:
                action_type = EventActionType(raw_action)
            except ValueError:
                return web.json_response(
                    {"error": f"Invalid action_type: {raw_action}"}, status=400
                )

        event = WebhookEvent(
            event_type=event_type,
            action_type=action_type,
            action_reference=action_ref,
            payload=body.get("data", {}),
            timestamp=datetime.now(),
        )
        self._events.append(event)

        logger.info(
            "Webhook event received: type=%s, action=%s",
            event_type,
            raw_action,
        )

        # 이벤트 처리 — 핸들러가 동기/비동기 모두 가능하므로 __await__ 여부로 분기
        if self._event_handler and action_type:
            try:
                result = self._event_handler(event)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.exception("Webhook event handler error")

        return web.json_response({
            "status": "accepted",
            "event_type": event_type,
        })
