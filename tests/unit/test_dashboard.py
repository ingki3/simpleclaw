"""Tests for the web dashboard."""

import pytest
from aiohttp import web

from simpleclaw.logging.dashboard import DashboardServer
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger


class TestDashboardServer:
    @pytest.fixture
    def dashboard(self, tmp_path):
        metrics = MetricsCollector()
        metrics.record_execution(success=True, duration_ms=100, tokens_used=50)
        metrics.record_execution(success=False, duration_ms=200)

        logger = StructuredLogger(log_dir=tmp_path / "logs")
        logger.log(action_type="test", status="success", duration_ms=100)

        return DashboardServer(metrics, logger)

    @pytest.mark.asyncio
    async def test_dashboard_html(self, dashboard, aiohttp_client):
        dashboard._app = web.Application()
        dashboard._app.router.add_get("/", dashboard._handle_dashboard)
        client = await aiohttp_client(dashboard._app)
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "SimpleClaw Dashboard" in text

    @pytest.mark.asyncio
    async def test_metrics_api(self, dashboard, aiohttp_client):
        dashboard._app = web.Application()
        dashboard._app.router.add_get("/api/metrics", dashboard._handle_metrics)
        client = await aiohttp_client(dashboard._app)
        resp = await client.get("/api/metrics")
        assert resp.status == 200
        data = await resp.json()
        assert data["total_executions"] == 2
        assert data["total_tokens_used"] == 50

    @pytest.mark.asyncio
    async def test_logs_api(self, dashboard, aiohttp_client):
        dashboard._app = web.Application()
        dashboard._app.router.add_get("/api/logs", dashboard._handle_logs)
        client = await aiohttp_client(dashboard._app)
        resp = await client.get("/api/logs")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["action_type"] == "test"
