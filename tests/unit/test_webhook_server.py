"""Tests for the webhook server."""

import asyncio

import pytest
from aiohttp import web

from simpleclaw.channels.webhook_server import (
    ANOMALY_CONSECUTIVE_BLOCKS,
    WebhookServer,
)


class TestWebhookServer:
    @pytest.fixture
    def server(self):
        return WebhookServer(
            host="127.0.0.1",
            port=0,  # Random port
            auth_token="test-secret",
        )

    @pytest.fixture
    def server_no_auth(self):
        return WebhookServer(
            host="127.0.0.1",
            port=0,
            auth_token="",
        )

    @pytest.mark.asyncio
    async def test_health_endpoint(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_get("/health", server._handle_health)
        client = await aiohttp_client(server._app)
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_webhook_valid_request(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)
        resp = await client.post(
            "/webhook",
            json={"event_type": "test_event", "data": {"key": "value"}},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "accepted"
        assert data["event_type"] == "test_event"

    @pytest.mark.asyncio
    async def test_webhook_unauthorized(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)
        resp = await client.post(
            "/webhook",
            json={"event_type": "test"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_webhook_missing_auth(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)
        resp = await client.post(
            "/webhook",
            json={"event_type": "test"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_webhook_no_auth_required(self, server_no_auth, aiohttp_client):
        server_no_auth._app = web.Application()
        server_no_auth._app.router.add_post("/webhook", server_no_auth._handle_webhook)
        client = await aiohttp_client(server_no_auth._app)
        resp = await client.post(
            "/webhook",
            json={"event_type": "test"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_webhook_invalid_json(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)
        resp = await client.post(
            "/webhook",
            data="not json",
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
            },
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_webhook_missing_event_type(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)
        resp = await client.post(
            "/webhook",
            json={"data": "no event type"},
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_webhook_with_action(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)
        resp = await client.post(
            "/webhook",
            json={
                "event_type": "trigger",
                "action_type": "prompt",
                "action_reference": "Hello world",
            },
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 200
        events = server.get_events()
        assert len(events) == 1
        assert events[0].action_reference == "Hello world"

    @pytest.mark.asyncio
    async def test_webhook_access_log(self, server, aiohttp_client):
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)

        # Valid request
        await client.post(
            "/webhook",
            json={"event_type": "test"},
            headers={"Authorization": "Bearer test-secret"},
        )
        # Invalid request
        await client.post(
            "/webhook",
            json={"event_type": "test"},
            headers={"Authorization": "Bearer wrong"},
        )

        log = server.get_access_log()
        assert len(log) == 2
        assert log[0].authorized is True
        assert log[1].authorized is False


# ---------------------------------------------------------------------------
# BIZ-24: 페이로드 크기 / Rate Limit / 동시성 cap / 비정상 트래픽 알림
# ---------------------------------------------------------------------------


class TestWebhookSecurityHardening:
    """BIZ-24 보안 정책 회귀 테스트.

    각 차단 유형(413/429/503)과 알림 경로가 의도대로 동작하는지 확인한다.
    """

    def _make_app(self, server: WebhookServer) -> web.Application:
        """server.start() 없이도 max_body_size 정책이 aiohttp 레벨에 반영되도록
        client_max_size를 동일하게 적용한 테스트용 Application을 만든다.
        """
        app = web.Application(client_max_size=server._max_body_size)
        app.router.add_post("/webhook", server._handle_webhook)
        server._app = app
        return app

    @pytest.mark.asyncio
    async def test_payload_too_large_returns_413(self, aiohttp_client):
        """Content-Length가 max_body_size를 초과하면 본문을 읽기 전 413으로 차단된다."""
        server = WebhookServer(
            host="127.0.0.1",
            port=0,
            auth_token="test-secret",
            max_body_size=256,  # 256 bytes
        )
        app = self._make_app(server)
        client = await aiohttp_client(app)

        # 256바이트를 넘는 더미 페이로드 — 키/값 길이로 의도적으로 초과시킴.
        big_payload = {"event_type": "test", "data": {"x": "A" * 1024}}
        resp = await client.post(
            "/webhook",
            json=big_payload,
            headers={"Authorization": "Bearer test-secret"},
        )
        assert resp.status == 413
        assert server.get_metrics().blocked_payload_too_large == 1
        # 차단 사유가 감사 로그에 reason과 함께 기록된다.
        log = server.get_access_log()
        assert log[-1].authorized is False
        assert "payload_too_large" in log[-1].details

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429_with_retry_after(
        self, aiohttp_client
    ):
        """슬라이딩 윈도우 한도를 넘으면 429 + Retry-After 헤더로 응답한다."""
        server = WebhookServer(
            host="127.0.0.1",
            port=0,
            auth_token="test-secret",
            rate_limit=2,
            rate_limit_window=60.0,
        )
        app = self._make_app(server)
        client = await aiohttp_client(app)

        ok = {"event_type": "test"}
        headers = {"Authorization": "Bearer test-secret"}

        # 윈도우 내 허용 한도(2회)까지는 통과.
        for _ in range(2):
            resp = await client.post("/webhook", json=ok, headers=headers)
            assert resp.status == 200

        # 3번째는 차단되며 Retry-After가 포함된다.
        resp = await client.post("/webhook", json=ok, headers=headers)
        assert resp.status == 429
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) >= 1
        assert server.get_metrics().blocked_rate_limited >= 1

    @pytest.mark.asyncio
    async def test_concurrency_queue_saturation_returns_503(self):
        """동시 처리 cap과 대기 큐가 모두 가득 차면 즉시 503 + Retry-After를 반환한다.

        실제 동시성 경합을 만들지 않고, 카운터를 직접 포화 상태로 셋업해 게이트만 검증한다.
        """
        server = WebhookServer(
            host="127.0.0.1",
            port=0,
            auth_token="",
            max_concurrent_connections=1,
            queue_size=1,
            rate_limit=0,  # rate limit 비활성 — 게이트만 보고 싶음
        )
        # 핸들러를 직접 호출하기 위한 더미 request — Content-Length는 None으로 둬서 사전 검사 통과.
        request = _DummyRequest(remote="1.2.3.4")

        # 강제 포화: inflight 1, waiting 1.
        server._inflight_count = 1
        server._waiting_count = 1
        server._semaphore = asyncio.Semaphore(1)

        resp = await server._handle_webhook(request)
        assert resp.status == 503
        assert resp.headers.get("Retry-After") == "1"
        m = server.get_metrics()
        assert m.blocked_concurrency == 1
        assert m.queue_full_events == 1

    @pytest.mark.asyncio
    async def test_anomaly_alert_callback_fires_after_consecutive_blocks(
        self, aiohttp_client
    ):
        """동일 IP에서 연속 차단(401)이 임계 도달하면 텔레그램용 알림 콜백이 호출된다."""
        calls: list[tuple[str, dict]] = []

        def alert_cb(alert_type: str, details: dict) -> None:
            calls.append((alert_type, dict(details)))

        server = WebhookServer(
            host="127.0.0.1",
            port=0,
            auth_token="test-secret",
            rate_limit=0,  # rate limit이 alert를 먼저 트리거하지 않도록 비활성
            alert_callback=alert_cb,
            alert_cooldown=0.0,  # 테스트에서 쿨다운으로 두 번째 트리거를 막지 않도록.
        )
        app = self._make_app(server)
        client = await aiohttp_client(app)

        # 임계만큼 연속으로 잘못된 토큰 → 모두 401, 마지막 시도에서 알림이 발사된다.
        for _ in range(ANOMALY_CONSECUTIVE_BLOCKS):
            resp = await client.post(
                "/webhook",
                json={"event_type": "test"},
                headers={"Authorization": "Bearer wrong"},
            )
            assert resp.status == 401

        # consecutive_blocks 알림이 최소 1회 발사되었는지 확인.
        types = [t for t, _ in calls]
        assert "consecutive_blocks" in types
        assert server.get_metrics().alerts_sent >= 1


class _DummyRequest:
    """test_concurrency_queue_saturation_returns_503에서 사용하는 최소 요청 더미.

    실제 aiohttp Request를 만들지 않고도 _handle_webhook의 게이트 분기를 검증한다.
    """

    def __init__(self, remote: str) -> None:
        self.remote = remote
        self.headers: dict[str, str] = {}
        self.content_length: int | None = None

    async def json(self) -> dict:  # pragma: no cover - 게이트 분기에서 도달하지 않음
        return {}
