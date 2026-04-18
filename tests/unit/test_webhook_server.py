"""Tests for the webhook server."""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from simpleclaw.channels.webhook_server import WebhookServer


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
