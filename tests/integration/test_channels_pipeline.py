"""Integration tests for communication channels."""

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
