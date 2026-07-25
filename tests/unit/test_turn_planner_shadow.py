"""BIZ-493 — Unified TurnPlanner shadow telemetry와 background 계약."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.plan_gate import GateStatus, PlanGateResult, PlanViolation
from simpleclaw.agent.turn_analysis import TurnAnalysis
from simpleclaw.agent.turn_plan import (
    AssetRef,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.agent.turn_planner_telemetry import (
    PlannerUsageCaptureRouter,
    aggregate_turn_planner_shadow_events,
    build_turn_planner_shadow_event,
    emit_turn_planner_shadow_event,
)
from simpleclaw.llm.models import LLMRequest, LLMResponse
from simpleclaw.logging.structured_logger import StructuredLogger
from simpleclaw.logging.trace_context import trace_scope


def _candidates(private_text: str = "PRIVATE_HISTORY") -> ContextCandidateSet:
    candidate = ContextCandidate(
        turn_id="msg:1",
        role="user",
        timestamp=datetime.now(UTC),
        content=private_text,
        trust=ContextTrust.USER_INPUT,
    )
    return ContextCandidateSet(
        candidates=(candidate,),
        total_chars=len(private_text),
        truncated=False,
    )


def _plan(private_text: str = "PRIVATE_CURRENT") -> UnifiedTurnPlan:
    asset = AssetRef("skill", "weather")
    return UnifiedTurnPlan(
        original_text=private_text,
        context=ContextSelection(
            relation=ContextRelation.SAME_THREAD,
            use_prior_context=True,
            selected_turn_ids=("msg:1",),
            standalone_question="PRIVATE_STANDALONE",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("weather",),
        intents=("forecast",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="weather",
            entities=("PRIVATE_ENTITY",),
            search_query="PRIVATE_QUERY",
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.FACT_CHECK,
            primary_asset=asset,
            allowed_assets=(asset,),
            allowed_tools=("execute_skill",),
            requires_confirmation=False,
            reason="PRIVATE_REASON",
        ),
        confidence=0.9,
        decision_summary="PRIVATE_SUMMARY",
        catalog_fingerprint="catalog-v1",
    )


@pytest.fixture
def config_file(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""\
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-test
      api_key: test-key
agent:
  history_limit: 8
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 2
  turn_analysis:
    enabled: true
  unified_turn_planner:
    mode: shadow
    sample_rate: 1.0
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: AGENT.md
      type: agent
memory:
  rag:
    enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent", encoding="utf-8")
    return config


def _prepare_current_pipeline(orch: AgentOrchestrator, monkeypatch) -> None:
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="질문",
                normalized_question="질문",
            )
        ),
    )
    orch._tool_loop = AsyncMock(return_value="현재 파이프라인 답변")


def test_shadow_event_contains_only_redacted_structured_fields():
    candidates = _candidates()
    plan = _plan()
    gate = PlanGateResult(
        status=GateStatus.REPAIR,
        effective_plan=None,
        violations=(
            PlanViolation("fact_check.required", "fact_check", "PRIVATE_MESSAGE"),
        ),
    )

    event = build_turn_planner_shadow_event(
        plan=plan,
        gate_result=gate,
        candidates=candidates,
        latency_ms=2480.4,
        input_tokens=3100,
        output_tokens=280,
    )
    serialized = event.to_json()

    assert json.loads(serialized) == {
        "asset_count": 1,
        "candidate_context_chars": 15,
        "candidate_turn_count": 1,
        "catalog_fingerprint": "catalog-v1",
        "error_code": None,
        "event": "unified_turn_plan_shadow",
        "execution_mode": "fact_check",
        "fact_required": True,
        "gate_status": "repair",
        "input_tokens": 3100,
        "latency_ms": 2480,
        "ok": True,
        "output_tokens": 280,
        "relation": "same_thread",
        "selected_context_chars": 15,
        "selected_turn_count": 1,
        "violation_codes": ["fact_check.required"],
    }
    for private in (
        "PRIVATE_HISTORY",
        "PRIVATE_CURRENT",
        "PRIVATE_STANDALONE",
        "PRIVATE_ENTITY",
        "PRIVATE_QUERY",
        "PRIVATE_REASON",
        "PRIVATE_SUMMARY",
        "PRIVATE_MESSAGE",
    ):
        assert private not in serialized


def test_structured_shadow_event_breaks_turn_trace_correlation(tmp_path):
    event = build_turn_planner_shadow_event(
        plan=_plan(),
        gate_result=PlanGateResult(GateStatus.PASS, _plan()),
        candidates=_candidates(),
        latency_ms=100,
    )
    structured_logger = StructuredLogger(tmp_path / "logs")
    synthetic_trace_id = "SYNTHETIC_TURN_TRACE_PRIVATE"

    with trace_scope(synthetic_trace_id):
        emit_turn_planner_shadow_event(
            event,
            structured_logger=structured_logger,
        )

    log_path = next((tmp_path / "logs").glob("execution_*.log"))
    raw_row = log_path.read_text(encoding="utf-8").strip()
    row = json.loads(raw_row)

    assert row["action_type"] == "unified_turn_plan_shadow"
    assert row["trace_id"] == ""
    assert synthetic_trace_id not in raw_row
    assert "PRIVATE_" not in raw_row


def test_shadow_event_aggregate_reports_gate_latency_tokens_and_context_reduction():
    candidates = _candidates()
    event = build_turn_planner_shadow_event(
        plan=_plan(),
        gate_result=PlanGateResult(GateStatus.PASS, _plan()),
        candidates=candidates,
        latency_ms=100,
        input_tokens=20,
        output_tokens=5,
    )

    report = aggregate_turn_planner_shadow_events([event])

    assert report["samples"] == 1
    assert report["gate_status"] == {"pass": 1}
    assert report["latency_ms"] == {"p50": 100.0, "p95": 100.0}
    assert report["tokens"]["input_total"] == 20
    assert report["tokens"]["output_total"] == 5
    assert report["context_reduction_rate"] == 0.0


@pytest.mark.asyncio
async def test_usage_capture_counts_primary_and_validated_retry_tokens():
    wrapped = MagicMock()
    primary = LLMResponse(
        text="invalid",
        backend_name="primary",
        usage={"input_tokens": 10, "output_tokens": 4},
    )
    retry = LLMResponse(
        text="valid",
        backend_name="retry",
        usage={"input_tokens": 3, "output_tokens": 2},
    )

    async def send_validated(_request, validate):
        with pytest.raises(ValueError):
            validate(primary)
        return validate(retry)

    wrapped.send_validated = AsyncMock(side_effect=send_validated)
    capture = PlannerUsageCaptureRouter(wrapped)

    result = await capture.send_validated(
        LLMRequest(),
        lambda response: (
            response.text
            if response.backend_name == "retry"
            else (_ for _ in ()).throw(ValueError("invalid"))
        ),
    )

    assert result == "valid"
    assert capture.input_tokens == 13
    assert capture.output_tokens == 6


@pytest.mark.asyncio
async def test_shadow_task_does_not_block_current_response(config_file, monkeypatch):
    orch = AgentOrchestrator(config_file)
    _prepare_current_pipeline(orch, monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_shadow(*args, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(orch, "_run_unified_turn_planner_shadow", blocked_shadow)
    response_task = asyncio.create_task(
        orch.process_message("질문", user_id=11, chat_id=22)
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    response = await asyncio.wait_for(response_task, timeout=0.2)

    assert response == "현재 파이프라인 답변"
    assert orch._background_tasks
    release.set()
    await asyncio.gather(*orch._background_tasks)


@pytest.mark.asyncio
async def test_shadow_exception_does_not_change_current_response(
    config_file, monkeypatch
):
    orch = AgentOrchestrator(config_file)
    _prepare_current_pipeline(orch, monkeypatch)
    planner = AsyncMock(side_effect=RuntimeError("PRIVATE_PROVIDER_BODY"))
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )

    with patch(
        "simpleclaw.agent.orchestrator.emit_turn_planner_shadow_event"
    ) as emit:
        response = await orch.process_message(
            "PRIVATE_CURRENT",
            user_id=11,
            chat_id=22,
        )
        await asyncio.gather(*orch._background_tasks)

    assert response == "현재 파이프라인 답변"
    planner.assert_awaited_once()
    event = emit.call_args.args[0]
    assert event.ok is False
    assert event.error_code == "planner_unavailable"
    assert "PRIVATE" not in event.to_json()


@pytest.mark.asyncio
async def test_sampled_out_shadow_does_not_call_planner(config_file, monkeypatch):
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace("sample_rate: 1.0", "sample_rate: 0.5"),
        encoding="utf-8",
    )
    orch = AgentOrchestrator(config_file)
    _prepare_current_pipeline(orch, monkeypatch)
    planner = AsyncMock()
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    monkeypatch.setattr("simpleclaw.agent.orchestrator.random.random", lambda: 0.9)

    response = await orch.process_message("질문", user_id=11, chat_id=22)
    await asyncio.sleep(0)

    assert response == "현재 파이프라인 답변"
    planner.assert_not_awaited()
    assert not orch._background_tasks
