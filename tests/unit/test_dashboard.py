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


class TestDashboardTraceTimeline:
    """trace_id 필터 + 타임라인 엔드포인트 (BIZ-25) 동작을 검증한다."""

    @pytest.fixture
    def dashboard_with_traces(self, tmp_path):
        from simpleclaw.logging.trace_context import trace_scope

        metrics = MetricsCollector()
        logger = StructuredLogger(log_dir=tmp_path / "logs")
        with trace_scope("trace-alpha"):
            logger.log(action_type="rag_retrieve", duration_ms=10)
            logger.log(action_type="skill_call", duration_ms=20)
        with trace_scope("trace-beta"):
            logger.log(action_type="rag_retrieve", duration_ms=15)
        return DashboardServer(metrics, logger)

    @pytest.mark.asyncio
    async def test_logs_api_filter_by_trace_id(
        self, dashboard_with_traces, aiohttp_client
    ):
        d = dashboard_with_traces
        d._app = web.Application()
        d._app.router.add_get("/api/logs", d._handle_logs)
        client = await aiohttp_client(d._app)
        resp = await client.get("/api/logs?trace_id=trace-alpha")
        assert resp.status == 200
        data = await resp.json()
        assert len(data) == 2
        assert {e["action_type"] for e in data} == {"rag_retrieve", "skill_call"}
        assert all(e["trace_id"] == "trace-alpha" for e in data)

    @pytest.mark.asyncio
    async def test_trace_endpoint_returns_timeline(
        self, dashboard_with_traces, aiohttp_client
    ):
        d = dashboard_with_traces
        d._app = web.Application()
        d._app.router.add_get("/api/trace", d._handle_trace)
        client = await aiohttp_client(d._app)
        resp = await client.get("/api/trace?trace_id=trace-alpha")
        assert resp.status == 200
        data = await resp.json()
        assert data["trace_id"] == "trace-alpha"
        assert data["count"] == 2
        # 시간순 정렬 검증
        timestamps = [s["timestamp"] for s in data["steps"]]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_trace_endpoint_requires_trace_id(
        self, dashboard_with_traces, aiohttp_client
    ):
        d = dashboard_with_traces
        d._app = web.Application()
        d._app.router.add_get("/api/trace", d._handle_trace)
        client = await aiohttp_client(d._app)
        resp = await client.get("/api/trace")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_trace_endpoint_unknown_trace(
        self, dashboard_with_traces, aiohttp_client
    ):
        d = dashboard_with_traces
        d._app = web.Application()
        d._app.router.add_get("/api/trace", d._handle_trace)
        client = await aiohttp_client(d._app)
        resp = await client.get("/api/trace?trace_id=nonexistent")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] == 0
        assert data["steps"] == []
