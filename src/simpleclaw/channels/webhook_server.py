"""웹훅 서버: aiohttp 기반 경량 REST 엔드포인트.

외부 시스템으로부터 이벤트를 수신하는 HTTP 서버를 제공한다.
- POST /webhook : Bearer 토큰 인증 → 이벤트 파싱 → 핸들러 디스패치
- GET /health   : 서버 상태 확인용 헬스체크
- 인증 실패·성공 모두 AccessAttempt으로 기록하여 감사 추적 가능

보안 강화 (BIZ-24):
- max_body_size: 페이로드 상한, 초과 시 413 (`Content-Length` 사전 검사 + aiohttp
  `client_max_size`로 스트리밍 본문 보호 이중 안전망)
- rate_limit: 토큰/IP별 슬라이딩 윈도우, 초과 시 429 + ``Retry-After``
- max_concurrent_connections + queue_size: 동시성 cap, 포화 시 503 + ``Retry-After``
- 비정상 트래픽(연속 차단, 단일 IP 폭주, 큐 포화) 텔레그램 알림 + 쿨다운(중복 억제)
- 모든 차단 이벤트는 ``AccessAttempt`` 감사 로그에 reason과 함께 기록되며,
  구조화 로거가 주입되면 ``action=webhook_block`` JSONL 항목으로도 남는다.

설계 노트:
- aiohttp는 단일 이벤트 루프에서 협업적으로 동작하므로, 카운터/딕셔너리 갱신은
  await 사이에서 원자적이다. 별도 lock 없이 `if check + mutate` 패턴이 안전.
- rate limit/anomaly 추적용 deque/dict는 처리량과 메모리 절충을 위해 호출 시점에
  만료 항목을 정리하는 lazy cleanup 방식을 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Deque

from aiohttp import web

from simpleclaw.channels.models import (
    AccessAttempt,
    EventActionType,
    WebhookEvent,
    WebhookError,
)

logger = logging.getLogger(__name__)


# 보안 정책 기본값 — config.yaml의 webhook 섹션에서 오버라이드 가능.
DEFAULT_MAX_BODY_SIZE = 1_048_576  # 1MB
DEFAULT_RATE_LIMIT = 60  # 요청 수 (0이면 비활성)
DEFAULT_RATE_LIMIT_WINDOW = 60.0  # 초
DEFAULT_MAX_CONCURRENT = 32
DEFAULT_QUEUE_SIZE = 64
DEFAULT_ALERT_COOLDOWN = 300.0  # 초

# 비정상 트래픽 임계값 — 운영 중 튜닝 필요 시 상수로 일괄 조정.
ANOMALY_CONSECUTIVE_BLOCKS = 5  # 동일 IP 연속 차단 횟수
ANOMALY_BURST_REQUESTS = 100  # 단일 IP 짧은 윈도우 내 요청 수
ANOMALY_BURST_WINDOW = 10.0  # 위 burst 측정 윈도우(초)


# 알림 콜백 시그니처 — (alert_type, details) → 동기/비동기 모두 허용.
AlertCallback = Callable[[str, dict], "Awaitable[None] | None"]


@dataclass
class WebhookMetrics:
    """웹훅 전용 카운터.

    대시보드/테스트 검증/알림 판정에 활용된다. 모든 필드는 단조 증가.
    """

    accepted: int = 0
    blocked_unauthorized: int = 0  # 401
    blocked_payload_too_large: int = 0  # 413
    blocked_rate_limited: int = 0  # 429
    blocked_concurrency: int = 0  # 503
    queue_full_events: int = 0
    alerts_sent: int = 0


class WebhookServer:
    """aiohttp 기반 경량 HTTP 웹훅 수신 서버.

    Bearer 토큰 인증, JSON 페이로드 파싱, 이벤트 핸들러 디스패치를 담당하며,
    페이로드 크기 제한·Rate Limit·동시 연결 cap·비정상 트래픽 알림 등 BIZ-24
    보안 강화 기능을 포함한다.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        auth_token: str = "",
        event_handler=None,
        *,
        max_body_size: int = DEFAULT_MAX_BODY_SIZE,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        rate_limit_window: float = DEFAULT_RATE_LIMIT_WINDOW,
        max_concurrent_connections: int = DEFAULT_MAX_CONCURRENT,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        alert_callback: AlertCallback | None = None,
        alert_cooldown: float = DEFAULT_ALERT_COOLDOWN,
        structured_logger: object | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._event_handler = event_handler

        # 정책 파라미터를 안전 범위로 강제 — 음수/0 입력으로 의도치 않은 비활성화 방지.
        self._max_body_size = max(1, int(max_body_size))
        self._rate_limit = max(0, int(rate_limit))  # 0이면 rate limit 비활성
        self._rate_limit_window = max(0.001, float(rate_limit_window))
        self._max_concurrent = max(1, int(max_concurrent_connections))
        self._queue_size = max(0, int(queue_size))
        self._alert_callback = alert_callback
        self._alert_cooldown = max(0.0, float(alert_cooldown))
        self._structured_logger = structured_logger

        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._running = False
        self._events: list[WebhookEvent] = []
        self._access_log: list[AccessAttempt] = []

        # 슬라이딩 윈도우 rate limit — key(token 또는 ip) → 타임스탬프 deque.
        self._rate_buckets: dict[str, Deque[float]] = defaultdict(deque)
        # 비정상 추적 — 연속 차단 카운터(차단 시 +1, 정상 통과 시 0으로 리셋)와
        # IP별 burst 윈도우(짧은 시간에 다수 요청 감지).
        self._consecutive_blocks: dict[str, int] = defaultdict(int)
        self._burst_windows: dict[str, Deque[float]] = defaultdict(deque)
        # 알림 쿨다운 — alert key → 마지막 발신 시각(monotonic).
        self._alert_last_sent: dict[str, float] = {}

        # 동시성 제어용 asyncio 객체는 이벤트 루프에서만 만들 수 있으므로 start()에서 초기화.
        self._semaphore: asyncio.Semaphore | None = None
        self._inflight_count: int = 0
        self._waiting_count: int = 0

        self._metrics = WebhookMetrics()

    async def start(self) -> None:
        """웹훅 HTTP 서버를 시작한다.

        ``client_max_size``를 max_body_size로 설정해 aiohttp 레벨에서도 큰 본문이
        메모리에 적재되기 전에 잘라낸다(스트리밍 안전망).
        """
        self._app = web.Application(client_max_size=self._max_body_size)
        self._app.router.add_post("/webhook", self._handle_webhook)
        self._app.router.add_get("/health", self._handle_health)

        # 이벤트 루프 컨텍스트에서 동시성 제어 객체 생성.
        self._semaphore = asyncio.Semaphore(self._max_concurrent)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        self._running = True
        logger.info(
            "Webhook server started on %s:%d "
            "(max_body=%dB, rate_limit=%d/%.1fs, max_concurrent=%d, queue=%d)",
            self._host,
            self._port,
            self._max_body_size,
            self._rate_limit,
            self._rate_limit_window,
            self._max_concurrent,
            self._queue_size,
        )

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

    def get_metrics(self) -> WebhookMetrics:
        """웹훅 보안/트래픽 카운터 스냅샷을 반환한다.

        대시보드 카드나 외부 메트릭 수집기에서 폴링용으로 사용한다.
        """
        return WebhookMetrics(**self._metrics.__dict__)

    # ------------------------------------------------------------------
    # 핸들러
    # ------------------------------------------------------------------

    async def _handle_health(self, request: web.Request) -> web.Response:
        """헬스체크 엔드포인트 — 서버 생존 확인용."""
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """수신된 웹훅 POST 요청을 처리한다.

        보안 검사 순서:
        1. Rate limit (429) — 인증 실패 케이스도 차단되도록 인증 전 평가
        2. 인증 (401)
        3. Content-Length 사전 검사 (413)
        4. 동시성 큐 게이트 (503)
        5. 본문 파싱·필수 필드 검증 + (부족 시) 본문 크기 사후 차단 (413)
        6. 핸들러 디스패치
        """
        remote = request.remote or "unknown"
        auth_header = request.headers.get("Authorization", "")
        auth_required = bool(self._auth_token)
        auth_ok = (not auth_required) or (
            auth_header == f"Bearer {self._auth_token}"
        )

        # rate limit 키 — 인증 통과 시 토큰별, 아니면 IP별로 추적해
        # 서로 다른 잘못된 토큰을 돌려쓰는 회피 시나리오에서도 IP가 차단되도록 한다.
        if auth_required and auth_ok:
            rate_key = f"token:{self._auth_token}"
        else:
            rate_key = f"ip:{remote}"

        # 1) Rate limit
        retry_after = self._check_rate_limit(rate_key)
        if retry_after is not None:
            self._metrics.blocked_rate_limited += 1
            self._record_block(remote, "rate_limited", f"key={rate_key}")
            self._note_consecutive_block(remote)
            self._maybe_alert(
                f"rate_limit:{remote}",
                "rate_limited",
                {"remote": remote, "key": rate_key, "retry_after": retry_after},
            )
            return self._build_error_response(
                429,
                "Too Many Requests",
                retry_after=retry_after,
            )

        # 2) 인증
        if not auth_ok:
            self._metrics.blocked_unauthorized += 1
            self._record_block(remote, "unauthorized", "Invalid or missing auth token")
            self._note_consecutive_block(remote)
            self._maybe_burst_alert(remote)
            logger.warning("Webhook unauthorized access from %s", remote)
            return self._build_error_response(401, "Unauthorized")

        # 3) Content-Length 사전 검사 — 본문을 메모리에 적재하기 전에 차단.
        content_length = request.content_length
        if content_length is not None and content_length > self._max_body_size:
            self._metrics.blocked_payload_too_large += 1
            self._record_block(
                remote,
                "payload_too_large",
                f"content_length={content_length} > max={self._max_body_size}",
            )
            self._note_consecutive_block(remote)
            return self._build_error_response(413, "Payload Too Large")

        # 4) 동시성 큐 게이트 — semaphore로 동시 처리 수를 제한하고 큐가 차면 503.
        sem = self._semaphore
        if sem is None:
            # start()를 거치지 않고 직접 핸들러를 라우터에 붙인 경우(테스트 등) 동적으로 보강.
            sem = asyncio.Semaphore(self._max_concurrent)
            self._semaphore = sem

        if (
            self._inflight_count >= self._max_concurrent
            and self._waiting_count >= self._queue_size
        ):
            # 큐 포화 — 즉시 503.
            self._metrics.blocked_concurrency += 1
            self._metrics.queue_full_events += 1
            self._record_block(remote, "concurrency_saturated", "queue full")
            self._note_consecutive_block(remote)
            self._maybe_alert(
                "queue_saturated",
                "queue_saturated",
                {
                    "inflight": self._inflight_count,
                    "waiting": self._waiting_count,
                    "max_concurrent": self._max_concurrent,
                    "queue_size": self._queue_size,
                },
            )
            return self._build_error_response(
                503, "Service Unavailable", retry_after=1
            )

        self._waiting_count += 1
        try:
            async with sem:
                self._waiting_count -= 1
                self._inflight_count += 1
                try:
                    return await self._process_authorized_request(
                        request, remote
                    )
                finally:
                    self._inflight_count -= 1
        except web.HTTPException:
            # aiohttp 예외(HTTPRequestEntityTooLarge 등)는 본문 수신 중 발생할 수 있음.
            raise
        except BaseException:
            # 세마포어 acquire 도중 취소된 경우 등 — waiting 카운터를 복구.
            if self._waiting_count > 0:
                self._waiting_count -= 1
            raise

    async def _process_authorized_request(
        self, request: web.Request, remote: str
    ) -> web.Response:
        """인증·정책 통과 후 실제 페이로드 파싱과 디스패치를 수행한다."""
        # 본문 읽기 — aiohttp의 client_max_size가 초과 시 HTTPRequestEntityTooLarge 발생.
        try:
            body = await request.json()
        except web.HTTPRequestEntityTooLarge:
            # 스트리밍 도중 본문 크기가 한도를 넘어선 경우 — Content-Length 검사로
            # 잡히지 않는 chunked 요청에 대비한 이중 안전망.
            self._metrics.blocked_payload_too_large += 1
            self._record_block(
                remote, "payload_too_large", "stream exceeded max_body_size"
            )
            self._note_consecutive_block(remote)
            return self._build_error_response(413, "Payload Too Large")
        except (json.JSONDecodeError, Exception):
            return self._build_error_response(400, "Invalid JSON payload")

        if not isinstance(body, dict):
            return self._build_error_response(400, "Payload must be a JSON object")

        # 필수 필드 검증
        event_type = body.get("event_type")
        if not event_type:
            return self._build_error_response(
                400, "Missing required field: event_type"
            )

        # 액션 타입 파싱
        action_type = None
        action_ref = body.get("action_reference", "")
        raw_action = body.get("action_type")
        if raw_action:
            try:
                action_type = EventActionType(raw_action)
            except ValueError:
                return self._build_error_response(
                    400, f"Invalid action_type: {raw_action}"
                )

        event = WebhookEvent(
            event_type=event_type,
            action_type=action_type,
            action_reference=action_ref,
            payload=body.get("data", {}),
            timestamp=datetime.now(),
        )
        self._events.append(event)

        # 정상 처리 — 인증 성공으로 감사 로그 기록 및 이상치 카운터 리셋.
        self._access_log.append(
            AccessAttempt(
                source="webhook",
                user_identifier=remote,
                authorized=True,
            )
        )
        self._consecutive_blocks[remote] = 0
        self._metrics.accepted += 1

        logger.info(
            "Webhook event received: type=%s, action=%s",
            event_type,
            raw_action,
        )

        # 이벤트 처리 — 핸들러가 동기/비동기 모두 가능하므로 __await__ 여부로 분기.
        if self._event_handler and action_type:
            try:
                result = self._event_handler(event)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.exception("Webhook event handler error")

        return web.json_response(
            {
                "status": "accepted",
                "event_type": event_type,
            }
        )

    # ------------------------------------------------------------------
    # Rate limit / anomaly helpers
    # ------------------------------------------------------------------

    def _check_rate_limit(self, key: str) -> int | None:
        """슬라이딩 윈도우 rate limit을 평가한다.

        통과하면 ``None``, 차단되어야 하면 ``Retry-After`` 헤더에 넣을 정수 초를 반환.
        ``rate_limit=0``이면 정책 비활성으로 항상 통과.
        """
        if self._rate_limit <= 0:
            return None

        now = time.monotonic()
        bucket = self._rate_buckets[key]
        # 윈도우 밖의 오래된 타임스탬프 제거.
        cutoff = now - self._rate_limit_window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self._rate_limit:
            # 가장 오래된 항목이 윈도우를 벗어날 때까지 대기 시간 산출(올림).
            wait_seconds = (bucket[0] + self._rate_limit_window) - now
            return max(1, int(math.ceil(wait_seconds)))

        bucket.append(now)
        return None

    def _note_consecutive_block(self, remote: str) -> None:
        """연속 차단 카운터를 1 증가시키고 임계 도달 시 알림을 발사한다."""
        self._consecutive_blocks[remote] += 1
        if self._consecutive_blocks[remote] >= ANOMALY_CONSECUTIVE_BLOCKS:
            self._maybe_alert(
                f"consecutive_blocks:{remote}",
                "consecutive_blocks",
                {
                    "remote": remote,
                    "count": self._consecutive_blocks[remote],
                    "threshold": ANOMALY_CONSECUTIVE_BLOCKS,
                },
            )

    def _maybe_burst_alert(self, remote: str) -> None:
        """단일 IP 폭주 패턴을 감지하면 알림을 발사한다.

        burst window 내 요청이 임계 이상이면 트리거. 인증 실패 직후에 호출되어
        '잘못된 토큰으로 빠르게 두드리는' 시나리오를 잡는다.
        """
        now = time.monotonic()
        window = self._burst_windows[remote]
        cutoff = now - ANOMALY_BURST_WINDOW
        while window and window[0] <= cutoff:
            window.popleft()
        window.append(now)
        if len(window) >= ANOMALY_BURST_REQUESTS:
            self._maybe_alert(
                f"burst:{remote}",
                "burst",
                {
                    "remote": remote,
                    "count": len(window),
                    "window_seconds": ANOMALY_BURST_WINDOW,
                },
            )

    def _maybe_alert(self, key: str, alert_type: str, details: dict) -> None:
        """쿨다운을 적용해 비정상 트래픽 알림을 발사한다.

        같은 ``key``에 대해 ``alert_cooldown`` 초 안에 연속 호출되면 무시되어
        텔레그램 채널이 같은 사건으로 도배되는 것을 방지한다.
        """
        if self._alert_callback is None:
            return
        now = time.monotonic()
        last = self._alert_last_sent.get(key, 0.0)
        if now - last < self._alert_cooldown:
            return
        self._alert_last_sent[key] = now
        self._metrics.alerts_sent += 1

        # 구조화 로거가 주입된 경우 사후 추적용 로그를 남김.
        self._emit_structured_log(
            "webhook_alert",
            {"alert_type": alert_type, "key": key, **details},
        )

        try:
            result = self._alert_callback(alert_type, details)
            # 비동기 콜백이면 백그라운드 태스크로 띄워 핸들러 응답을 막지 않는다.
            if hasattr(result, "__await__"):
                try:
                    asyncio.get_running_loop().create_task(result)  # type: ignore[arg-type]
                except RuntimeError:
                    # 이벤트 루프 밖에서 호출된 경우(테스트 등) — 무시.
                    pass
        except Exception:
            logger.exception("Webhook alert callback failed")

    def _record_block(self, remote: str, reason: str, details: str) -> None:
        """차단 결정을 ``AccessAttempt`` 감사 로그와 구조화 로그에 모두 기록한다."""
        self._access_log.append(
            AccessAttempt(
                source="webhook",
                user_identifier=remote,
                authorized=False,
                details=f"{reason}: {details}",
            )
        )
        self._emit_structured_log(
            "webhook_block",
            {"reason": reason, "remote": remote, "details": details},
        )

    def _emit_structured_log(self, action: str, details: dict) -> None:
        """주입된 ``StructuredLogger``가 있을 때만 trace_id 연계 JSONL을 남긴다."""
        slog = self._structured_logger
        if slog is None:
            return
        log_fn = getattr(slog, "log", None)
        if not callable(log_fn):
            return
        try:
            log_fn(
                action_type=action,
                input_summary=details.get("remote", ""),
                output_summary=details.get("reason", action),
                status="blocked" if action == "webhook_block" else "alert",
                level="WARNING",
                **details,
            )
        except Exception:
            logger.exception("Webhook structured log emission failed")

    # ------------------------------------------------------------------
    # 응답 빌더
    # ------------------------------------------------------------------

    def _build_error_response(
        self,
        status: int,
        message: str,
        *,
        retry_after: int | None = None,
    ) -> web.Response:
        """일관된 형식의 JSON 에러 응답을 만든다.

        429/503에는 ``Retry-After`` 헤더를 함께 실어 클라이언트가 백오프할 수 있도록 한다.
        """
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        return web.json_response(
            {"error": message}, status=status, headers=headers
        )


__all__ = ["WebhookServer", "WebhookMetrics", "WebhookError"]
