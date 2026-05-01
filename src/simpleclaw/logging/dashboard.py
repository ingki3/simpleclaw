"""에이전트 모니터링용 경량 웹 대시보드.

aiohttp 기반 단일 페이지 대시보드를 제공한다.
- GET /                  : 대시보드 HTML (메트릭 + 최근 실행 이력 + 메모리 분포 + trace 타임라인)
- GET /api/metrics       : MetricsCollector 스냅샷 JSON
- GET /api/logs          : StructuredLogger 엔트리 JSON (날짜·건수·trace_id 필터)
- GET /api/trace         : 단일 trace_id에 속한 로그를 시간 정렬한 타임라인 JSON (BIZ-25)
- GET /api/memory_stats  : 임베딩/클러스터 분포 + 최근 N일 RAG 회상 집계 JSON (BIZ-29)
프론트엔드는 10초 주기로 자동 갱신된다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from aiohttp import web

from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.stats import analyze_rag_logs, compute_distribution_stats

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
        .trace-id { font-family: monospace; font-size: 0.85em; color: #6b21a8; cursor: pointer; }
        .trace-id:hover { text-decoration: underline; }
        .trace-controls { margin-bottom: 12px; display: flex; gap: 8px; align-items: center; }
        .trace-controls input { flex: 1; padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; }
        .trace-controls button { padding: 6px 14px; border: none; border-radius: 4px; background: #2563eb; color: white; cursor: pointer; }
        .trace-controls button:hover { background: #1d4ed8; }
        .trace-controls button.secondary { background: #6b7280; }
        .timeline-step { padding: 8px 12px; margin: 4px 0; border-left: 3px solid #2563eb; background: #f8fafc; }
        .timeline-step.failure { border-left-color: #dc2626; }
        .timeline-step .meta { font-size: 0.85em; color: #666; margin-top: 4px; }
    </style>
</head>
<body>
    <h1>SimpleClaw Dashboard</h1>
    <div class="card" id="metrics">Loading...</div>
    <div class="card">
        <h2>Memory Index (BIZ-29)</h2>
        <div id="memory">Loading...</div>
    </div>
    <div class="card">
        <h2>Trace Timeline (BIZ-25)</h2>
        <div class="trace-controls">
            <input id="trace-input" type="text" placeholder="trace_id를 입력하거나 아래 표에서 클릭하세요" />
            <button onclick="loadTrace()">Search</button>
            <button class="secondary" onclick="clearTrace()">Clear</button>
        </div>
        <div id="timeline"><p>trace_id를 입력하면 동일 trace의 로그를 시간순으로 표시합니다.</p></div>
    </div>
    <div class="card">
        <h2>Recent Executions</h2>
        <div id="executions">Loading...</div>
    </div>
    <script>
        async function load() {
            const m = await (await fetch('/api/metrics')).json();
            // process_group_leaks > 0이면 빨간 카드로 시각적 경고.
            const leakCls = (m.process_group_leaks > 0) ? 'metric error' : 'metric';
            document.getElementById('metrics').innerHTML = `
                <div class="metric"><div class="value">${m.total_executions}</div><div class="label">Total Executions</div></div>
                <div class="metric"><div class="value">${m.successful_executions}</div><div class="label">Successful</div></div>
                <div class="metric error"><div class="value">${m.failed_executions}</div><div class="label">Failed</div></div>
                <div class="metric"><div class="value">${m.total_tokens_used}</div><div class="label">Tokens Used</div></div>
                <div class="metric"><div class="value">${m.active_cron_jobs}</div><div class="label">Active Cron Jobs</div></div>
                <div class="metric"><div class="value">${m.sub_agent_spawns}</div><div class="label">Sub-Agent Spawns</div></div>
                <div class="metric"><div class="value">${(m.error_rate * 100).toFixed(1)}%</div><div class="label">Error Rate</div></div>
                <div class="metric"><div class="value">${m.process_kills_sigterm}</div><div class="label">SIGTERM Kills</div></div>
                <div class="metric"><div class="value">${m.process_kills_sigkill}</div><div class="label">SIGKILL Kills</div></div>
                <div class="${leakCls}"><div class="value">${m.process_group_leaks}</div><div class="label">Process Group Leaks</div></div>
                <div class="metric"><div class="value">${m.zombies_reaped}</div><div class="label">Zombies Reaped</div></div>
            `;

            try {
                const ms = await (await fetch('/api/memory_stats')).json();
                if (ms.disabled) {
                    document.getElementById('memory').innerHTML =
                        '<p>Memory stats unavailable (no ConversationStore wired).</p>';
                } else {
                    const d = ms.distribution;
                    const r = ms.rag || { total_calls: 0, hit_rate: 0, avg_recalled_tokens: 0 };
                    const dimWarn = d.has_dimension_inconsistency
                        ? ' <span style="color:#dc2626;">⚠ mixed</span>'
                        : '';
                    document.getElementById('memory').innerHTML = `
                        <div class="metric"><div class="value">${d.total_messages}</div><div class="label">Total Messages</div></div>
                        <div class="metric"><div class="value">${d.coverage_percent}%</div><div class="label">Embedding Coverage</div></div>
                        <div class="metric"><div class="value">${d.cluster_count}</div><div class="label">Clusters</div></div>
                        <div class="metric"><div class="value">${d.unclustered_with_embedding}</div><div class="label">Unclustered (w/ emb)</div></div>
                        <div class="metric"><div class="value">${d.members.mean}</div><div class="label">Avg Members / Cluster</div></div>
                        <div class="metric"><div class="value">${Object.keys(d.embedding_dimensions).join(', ') || '–'}${dimWarn}</div><div class="label">Embedding Dim</div></div>
                        <div class="metric"><div class="value">${r.total_calls}</div><div class="label">RAG Calls (${ms.rag_days || 7}d)</div></div>
                        <div class="metric"><div class="value">${(r.hit_rate * 100).toFixed(1)}%</div><div class="label">RAG Hit Rate</div></div>
                        <div class="metric"><div class="value">${r.avg_recalled_tokens}</div><div class="label">Avg Recalled Tokens</div></div>
                    `;
                }
            } catch (e) {
                document.getElementById('memory').innerHTML =
                    '<p>Memory stats endpoint failed: ' + e + '</p>';
            }

            const entries = await (await fetch('/api/logs')).json();
            if (entries.length === 0) {
                document.getElementById('executions').innerHTML = '<p>No executions yet.</p>';
            } else {
                let html = '<table><tr><th>Time</th><th>Action</th><th>Status</th><th>Duration</th><th>Trace</th></tr>';
                entries.forEach(e => {
                    const cls = e.status === 'success' ? 'status-success' : 'status-failure';
                    const t = e.timestamp.split('T')[1]?.split('.')[0] || '';
                    const tid = e.trace_id || '';
                    const tidShort = tid ? tid.slice(0, 8) : '–';
                    const tidCell = tid
                        ? `<span class="trace-id" onclick="setTrace('${tid}')" title="${tid}">${tidShort}</span>`
                        : tidShort;
                    html += `<tr><td>${t}</td><td>${e.action_type}</td><td class="${cls}">${e.status}</td><td>${e.duration_ms}ms</td><td>${tidCell}</td></tr>`;
                });
                html += '</table>';
                document.getElementById('executions').innerHTML = html;
            }
        }
        function setTrace(tid) {
            document.getElementById('trace-input').value = tid;
            loadTrace();
        }
        function clearTrace() {
            document.getElementById('trace-input').value = '';
            document.getElementById('timeline').innerHTML =
                '<p>trace_id를 입력하면 동일 trace의 로그를 시간순으로 표시합니다.</p>';
        }
        async function loadTrace() {
            const tid = document.getElementById('trace-input').value.trim();
            const target = document.getElementById('timeline');
            if (!tid) {
                target.innerHTML = '<p>trace_id를 입력하세요.</p>';
                return;
            }
            try {
                const data = await (await fetch('/api/trace?trace_id=' + encodeURIComponent(tid))).json();
                const steps = data.steps || [];
                if (steps.length === 0) {
                    target.innerHTML = `<p>trace_id <code>${tid}</code>에 해당하는 로그가 없습니다.</p>`;
                    return;
                }
                let html = `<p><strong>${steps.length}</strong> steps · trace_id <code>${tid}</code></p>`;
                steps.forEach(s => {
                    const cls = s.status === 'success' ? 'timeline-step' : 'timeline-step failure';
                    const t = s.timestamp.split('T')[1]?.split('.')[0] || s.timestamp;
                    html += `<div class="${cls}"><strong>${s.action_type}</strong> <span class="${s.status === 'success' ? 'status-success' : 'status-failure'}">${s.status}</span>`;
                    html += `<div class="meta">${t} · ${s.duration_ms}ms · ${s.input_summary ? s.input_summary.slice(0, 80) : ''}</div></div>`;
                });
                target.innerHTML = html;
            } catch (e) {
                target.innerHTML = '<p>Trace timeline fetch failed: ' + e + '</p>';
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
        *,
        conversation_store: ConversationStore | None = None,
        rag_log_window_days: int = 7,
    ) -> None:
        self._metrics = metrics
        self._logger = structured_logger
        self._host = host
        self._port = port
        # 메모리 통계 노출용 — None이면 /api/memory_stats가 disabled 응답을 반환한다.
        # 분석 자체는 ConversationStore와 StructuredLogger만 있으면 가능하므로
        # 별도의 인스턴스를 주입할 필요는 없다.
        self._conversation_store = conversation_store
        self._rag_log_window_days = rag_log_window_days
        self._runner: web.AppRunner | None = None
        self._app: web.Application | None = None
        self._running = False

    async def start(self) -> None:
        """대시보드 HTTP 서버를 시작한다."""
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_dashboard)
        self._app.router.add_get("/api/metrics", self._handle_metrics)
        self._app.router.add_get("/api/logs", self._handle_logs)
        self._app.router.add_get("/api/trace", self._handle_trace)
        self._app.router.add_get("/api/memory_stats", self._handle_memory_stats)

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
        """구조화된 로그 엔트리를 JSON 배열로 반환한다.

        ``trace_id`` 쿼리 파라미터가 주어지면 해당 trace에 속한 항목만 반환한다.
        """
        date = request.query.get("date")
        limit = int(request.query.get("limit", "50"))
        trace_id = request.query.get("trace_id") or None
        entries = self._logger.get_entries(date=date, limit=limit, trace_id=trace_id)
        return web.json_response([e.to_dict() for e in entries])

    async def _handle_trace(self, request: web.Request) -> web.Response:
        """단일 trace_id에 속한 로그를 시간 순서로 정렬해 타임라인 형태로 반환한다.

        파라미터:
            trace_id (필수): 조회할 trace 식별자.
            date (선택): YYYYMMDD. 미지정 시 오늘 — 자정 경계에 걸친 trace는 정확히
                ``date``를 지정해 호출해야 한다.
            limit (선택): 최대 반환 개수(기본 500).

        ``trace_id``가 비어 있으면 400을 반환한다.
        """
        trace_id = (request.query.get("trace_id") or "").strip()
        if not trace_id:
            return web.json_response(
                {"error": "trace_id query parameter is required"},
                status=400,
            )
        date = request.query.get("date")
        try:
            limit = int(request.query.get("limit", "500"))
        except ValueError:
            limit = 500

        entries = self._logger.get_entries(date=date, limit=limit, trace_id=trace_id)
        # 파일은 시간순으로 기록되지만, 타임라인 표시 일관성을 위해 timestamp로
        # 한 번 더 정렬한다(다중 프로세스가 동시 기록할 때 순서가 흔들릴 수 있음).
        steps = sorted(
            (e.to_dict() for e in entries),
            key=lambda d: d.get("timestamp", ""),
        )
        return web.json_response({
            "trace_id": trace_id,
            "count": len(steps),
            "steps": steps,
        })

    async def _handle_memory_stats(self, request: web.Request) -> web.Response:
        """임베딩/클러스터 분포 + 최근 N일 RAG 회상 집계를 JSON으로 반환한다.

        ``ConversationStore``가 주입되지 않았다면 ``{"disabled": true}``로 응답한다.
        ``days`` 쿼리 파라미터로 RAG 분석 윈도우를 조정할 수 있다.
        """
        if self._conversation_store is None:
            return web.json_response({"disabled": True})

        try:
            days = int(request.query.get("days", str(self._rag_log_window_days)))
        except ValueError:
            days = self._rag_log_window_days

        try:
            distribution = compute_distribution_stats(self._conversation_store)
        except Exception as exc:  # noqa: BLE001 — 대시보드는 파일 동시쓰기 등 부수 오류에 강건해야 함
            logger.warning("Memory distribution stats failed: %s", exc)
            return web.json_response(
                {"disabled": False, "error": str(exc)},
                status=500,
            )

        rag_payload: dict | None = None
        try:
            rag_result = analyze_rag_logs(self._logger.log_dir, days=days)
            rag_payload = rag_result.to_dict()
        except Exception as exc:  # noqa: BLE001 — RAG 로그 누락은 분포 통계와 분리
            logger.warning("RAG log analysis failed: %s", exc)

        return web.json_response({
            "distribution": distribution.to_dict(),
            "rag": rag_payload,
            "rag_days": days,
        })
