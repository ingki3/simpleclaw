"""Tests for the web dashboard."""

from datetime import UTC, datetime

import pytest
from aiohttp import web

from simpleclaw.llm.usage import (
    LLMUsageEvent,
    NormalizedUsage,
    sanitize_usage_dimension,
)
from simpleclaw.logging.dashboard import DashboardServer, register_dashboard_routes
from simpleclaw.logging.llm_usage import LLMUsageStore
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
        assert "LLM Usage &amp; Cost" in text
        assert "llm-usage-period" in text

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

    @pytest.mark.asyncio
    async def test_register_dashboard_routes_adds_full_dashboard_contract(
        self, dashboard, aiohttp_client
    ):
        app = web.Application()
        register_dashboard_routes(
            app,
            metrics=dashboard._metrics,
            structured_logger=dashboard._logger,
        )
        client = await aiohttp_client(app)

        root = await client.get("/")
        assert root.status == 200
        assert "SimpleClaw Dashboard" in await root.text()

        metrics = await client.get("/api/metrics")
        assert metrics.status == 200
        assert (await metrics.json())["total_executions"] == 2

        logs = await client.get("/api/logs")
        assert logs.status == 200
        assert (await logs.json())[0]["action_type"] == "test"

        trace = await client.get("/api/trace?trace_id=missing")
        assert trace.status == 200
        assert (await trace.json())["steps"] == []

        memory = await client.get("/api/memory_stats")
        assert memory.status == 200
        assert await memory.json() == {"disabled": True}
        usage = await client.get("/api/llm-usage")
        assert usage.status == 200
        assert await usage.json() == {"disabled": True}

    @pytest.mark.asyncio
    async def test_usage_api_summary_and_validation(self, tmp_path, aiohttp_client):
        store = LLMUsageStore(tmp_path / "usage.db")
        store.record(
            LLMUsageEvent(
                "one",
                datetime.now(UTC).isoformat(),
                "",
                "primary",
                "profile",
                "model",
                "default",
                "chat",
                "primary",
                None,
                "success",
                1,
                NormalizedUsage(10, 2),
                14,
                "v1",
            )
        )
        dashboard = DashboardServer(
            MetricsCollector(), StructuredLogger(tmp_path / "logs"), usage_store=store
        )
        app = web.Application()
        dashboard.register_routes(app)
        client = await aiohttp_client(app)
        response = await client.get("/api/llm-usage?period=day&group_by=backend")
        payload = await response.json()
        assert payload["disabled"] is False
        assert payload["estimated_cost_usd"] == "0.000014"
        assert payload["groups"][0]["backend_name"] == sanitize_usage_dimension(
            "primary", field="backend_name"
        )
        assert (await client.get("/api/llm-usage?group_by=sql")).status == 400

    @pytest.mark.asyncio
    async def test_usage_api_never_returns_unsafe_dimension_markers(
        self, tmp_path, aiohttp_client
    ):
        markers = (
            "private-user-message-marker-7f3a",
            "AKIAFAKESYNTHETIC1234",
            "ghp_FAKE_SYNTHETIC_MARKER_1234567890",
            "xoxbFAKESYNTHETIC1234567890",
        )
        store = LLMUsageStore(tmp_path / "usage.db")
        store.record(
            LLMUsageEvent(
                markers[0],
                datetime.now(UTC).isoformat(),
                markers[1],
                markers[2],
                markers[3],
                markers[0],
                markers[1],
                markers[2],
                markers[3],
                markers[0],
                markers[1],
                1,
                NormalizedUsage(1, 1),
                1,
                markers[2],
                markers[3],
            )
        )
        dashboard = DashboardServer(
            MetricsCollector(),
            StructuredLogger(tmp_path / "logs"),
            usage_store=store,
        )
        app = web.Application()
        dashboard.register_routes(app)
        client = await aiohttp_client(app)

        for group_by in ("backend", "model", "route", "task"):
            response = await client.get(
                f"/api/llm-usage?period=day&group_by={group_by}"
            )
            body = await response.text()
            assert response.status == 200
            assert all(marker not in body for marker in markers)


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
