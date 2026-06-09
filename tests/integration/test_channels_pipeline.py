"""Integration tests for communication channels."""

import asyncio

import pytest

from simpleclaw.channels import TelegramBot, WebhookServer


class TestChannelsPipeline:
    @pytest.mark.asyncio
    async def test_telegram_bot_full_flow(self):
        """Test telegram bot authorization and message handling flow."""
        bot = TelegramBot(
            bot_token="fake-token",
            whitelist_user_ids=[100, 200],
            whitelist_chat_ids=[300],
        )

        # Authorized user
        resp = await bot.handle_message("Hello", 100, 999)
        assert resp is not None

        # Authorized chat
        resp = await bot.handle_message("Hello", 999, 300)
        assert resp is not None

        # Unauthorized
        resp = await bot.handle_message("Hello", 999, 999)
        assert resp is None

        # Check log
        log = bot.get_access_log()
        assert len(log) == 3
        assert sum(1 for a in log if a.authorized) == 2
        assert sum(1 for a in log if not a.authorized) == 1

    @pytest.mark.asyncio
    async def test_webhook_server_lifecycle(self, aiohttp_client):
        """Test webhook server start, handle events, and track state."""
        server = WebhookServer(
            host="127.0.0.1",
            port=0,
            auth_token="integration-token",
        )

        from aiohttp import web
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        server._app.router.add_get("/health", server._handle_health)
        client = await aiohttp_client(server._app)

        # Health check
        resp = await client.get("/health")
        assert resp.status == 200

        # Send events
        for i in range(3):
            resp = await client.post(
                "/webhook",
                json={"event_type": f"event_{i}", "data": {"index": i}},
                headers={"Authorization": "Bearer integration-token"},
            )
            assert resp.status == 200

        events = server.get_events()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_webhook_consecutive_blocks_alert_and_cooldown(self, aiohttp_client):
        """잘못된 token 연속 차단 시 alert callback이 1회만 발사된다."""
        from aiohttp import web

        alerts: list[tuple[str, dict]] = []
        server = WebhookServer(
            auth_token="integration-token",
            alert_callback=lambda alert_type, details: alerts.append(
                (alert_type, details)
            ),
            alert_cooldown=300.0,
        )
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        client = await aiohttp_client(server._app)

        for _ in range(5):
            resp = await client.post(
                "/webhook",
                json={"event_type": "bad"},
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status == 401

        assert len(alerts) == 1
        assert alerts[0][0] == "consecutive_blocks"
        assert alerts[0][1]["count"] == 5

        resp = await client.post(
            "/webhook",
            json={"event_type": "bad"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status == 401
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_webhook_queue_saturation_alert(self, aiohttp_client):
        """동시성 큐 포화 시 queue_saturated alert가 발사된다."""
        from aiohttp import web

        alerts: list[tuple[str, dict]] = []
        server = WebhookServer(
            auth_token="integration-token",
            max_concurrent_connections=1,
            queue_size=0,
            alert_callback=lambda alert_type, details: alerts.append(
                (alert_type, details)
            ),
        )
        server._app = web.Application()
        server._app.router.add_post("/webhook", server._handle_webhook)
        # 큐가 이미 포화된 상황을 직접 만들어 queue gate만 검증한다.
        server._semaphore = asyncio.Semaphore(1)
        server._inflight_count = 1
        server._waiting_count = 0
        client = await aiohttp_client(server._app)

        resp = await client.post(
            "/webhook",
            json={"event_type": "queued"},
            headers={"Authorization": "Bearer integration-token"},
        )

        assert resp.status == 503
        assert any(alert_type == "queue_saturated" for alert_type, _ in alerts)
