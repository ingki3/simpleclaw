"""에이전트 모니터링용 경량 웹 대시보드.

aiohttp 기반 단일 페이지 대시보드를 제공한다.
- GET /           : 대시보드 HTML (메트릭 + 최근 실행 이력)
- GET /api/metrics: MetricsCollector 스냅샷 JSON
- GET /api/logs   : StructuredLogger 엔트리 JSON (날짜·건수 필터)
프론트엔드는 10초 주기로 자동 갱신된다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from aiohttp import web

from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger

logger = logging.getLogger(__name__)

_DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>SimpleClaw Dashboard</title>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #f5f5f5; }
        h1 { color: #333; }
        .card { background: white; border-radius: 8px; padding: 20px; margin: 16px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .metric { display: inline-block; margin: 10px 20px 10px 0; }
        .metric .value { font-size: 2em; font-weight: bold; color: #2563eb; }
        .metric .label { font-size: 0.85em; color: #666; }
        .error .value { color: #dc2626; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee; }
        th { background: #f8f8f8; font-weight: 600; }
        .status-success { color: #16a34a; }
        .status-failure { color: #dc2626; }
    </style>
</head>
<body>
    <h1>SimpleClaw Dashboard</h1>
    <div class="card" id="metrics">Loading...</div>
    <div class="card">
        <h2>Recent Executions</h2>
        <div id="executions">Loading...</div>
    </div>
    <script>
        async function load() {
            const m = await (await fetch('/api/metrics')).json();
            document.getElementById('metrics').innerHTML = `
                <div class="metric"><div class="value">${m.total_executions}</div><div class="label">Total Executions</div></div>
                <div class="metric"><div class="value">${m.successful_executions}</div><div class="label">Successful</div></div>
                <div class="metric error"><div class="value">${m.failed_executions}</div><div class="label">Failed</div></div>
                <div class="metric"><div class="value">${m.total_tokens_used}</div><div class="label">Tokens Used</div></div>
                <div class="metric"><div class="value">${m.active_cron_jobs}</div><div class="label">Active Cron Jobs</div></div>
                <div class="metric"><div class="value">${m.sub_agent_spawns}</div><div class="label">Sub-Agent Spawns</div></div>
                <div class="metric"><div class="value">${(m.error_rate * 100).toFixed(1)}%</div><div class="label">Error Rate</div></div>
            `;
            const entries = await (await fetch('/api/logs')).json();
            if (entries.length === 0) {
                document.getElementById('executions').innerHTML = '<p>No executions yet.</p>';
            } else {
                let html = '<table><tr><th>Time</th><th>Action</th><th>Status</th><th>Duration</th></tr>';
                entries.forEach(e => {
                    const cls = e.status === 'success' ? 'status-success' : 'status-failure';
                    html += `<tr><td>${e.timestamp.split('T')[1]?.split('.')[0] || ''}</td><td>${e.action_type}</td><td class="${cls}">${e.status}</td><td>${e.duration_ms}ms</td></tr>`;
                });
                html += '</table>';
                document.getElementById('executions').innerHTML = html;
            }
        }
        load();
        setInterval(load, 10000);
    </script>
</body>
</html>"""


class DashboardServer:
    """aiohttp 기반 경량 웹 대시보드 서버.

    MetricsCollector와 StructuredLogger를 주입받아 API 엔드포인트로 노출한다.
    """

    def __init__(
        self,
        metrics: MetricsCollector,
        structured_logger: StructuredLogger,
        host: str = "127.0.0.1",
        port: int = 8081,
    ) -> None:
        self._metrics = metrics
        self._logger = structured_logger
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._app: web.Application | None = None
        self._running = False

    async def start(self) -> None:
        """대시보드 HTTP 서버를 시작한다."""
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_dashboard)
        self._app.router.add_get("/api/metrics", self._handle_metrics)
        self._app.router.add_get("/api/logs", self._handle_logs)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._running = True
        logger.info("Dashboard started on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        """대시보드 HTTP 서버를 중지한다."""
        if self._runner:
            await self._runner.cleanup()
        self._running = False
        logger.info("Dashboard stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        """대시보드 HTML 페이지를 반환한다."""
        return web.Response(text=_DASHBOARD_HTML, content_type="text/html")

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """현재 메트릭 스냅샷을 JSON으로 반환한다."""
        snapshot = self._metrics.get_snapshot()
        return web.json_response(snapshot.to_dict())

    async def _handle_logs(self, request: web.Request) -> web.Response:
        """구조화된 로그 엔트리를 JSON 배열로 반환한다."""
        date = request.query.get("date")
        limit = int(request.query.get("limit", "50"))
        entries = self._logger.get_entries(date=date, limit=limit)
        return web.json_response([e.to_dict() for e in entries])
