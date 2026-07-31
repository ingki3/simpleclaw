"""BIZ-496 — Unified complex-fact plan ownership 계약."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.evidence_retrieval import EvidenceRetriever
from simpleclaw.agent.fact_plan import fact_plan_from_turn_plan
from simpleclaw.agent.fact_types import (
    ComplexFactResult,
    EvidenceCoverage,
    EvidenceItem,
)
from simpleclaw.agent.fact_workflow import (
    ComplexFactWorkflow,
    ComplexFactWorkflowConfig,
)
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.turn_plan import (
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)


def _plan(
    *,
    fingerprint: str = "",
    claims: tuple[str, ...] = ("SK-NVIDIA 계약 체결 여부", "공식 발표 날짜"),
    allowed_tools: tuple[str, ...] = ("web_search",),
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="이전 답변 말고 지금 공식 근거로 다시 확인해줘",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="SK와 NVIDIA의 협력 발표를 공식 근거로 확인해줘",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("corporate_disclosure",),
        intents=("current_fact",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="corporate_disclosure",
            entities=("SK", "NVIDIA"),
            search_query="SK NVIDIA official announcement",
            required_claims=claims,
            freshness_required=True,
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.COMPLEX_FACT,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=allowed_tools,
            requires_confirmation=False,
            reason="multiple claims require evidence slots",
        ),
        confidence=0.95,
        decision_summary="complex current fact",
        catalog_fingerprint=fingerprint,
    )


def _config(tmp_path, *, hardening_ready: bool = False):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-2.0-flash
      api_key: test-key
agent:
  db_path: "{tmp_path}/conversations.db"
  unified_turn_planner:
    mode: primary
  complex_fact_workflow:
    source_claim_hardening_ready: {str(hardening_ready).lower()}
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
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
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent", encoding="utf-8")
    return config


def test_required_claims_are_losslessly_adapted_without_keyword_slots() -> None:
    plan = fact_plan_from_turn_plan(_plan(), max_iterations=5)

    assert plan.question == "SK와 NVIDIA의 협력 발표를 공식 근거로 확인해줘"
    assert [slot.name for slot in plan.slots] == ["claim_1", "claim_2"]
    assert [slot.question for slot in plan.slots] == [
        "SK-NVIDIA 계약 체결 여부",
        "공식 발표 날짜",
    ]
    assert "current_state" not in {slot.name for slot in plan.slots}


@pytest.mark.asyncio
async def test_missing_slots_iterate_and_return_limited_answer_without_composer() -> None:
    retriever = AsyncMock()
    retriever.search_for_slot.return_value = []
    composer = AsyncMock(return_value="지원되지 않는 숫자 999를 확정합니다.")
    workflow = ComplexFactWorkflow(
        retriever=retriever,
        compose_answer=composer,
        config=ComplexFactWorkflowConfig(max_iterations=3),
    )

    result = await workflow.run_turn_plan(_plan(claims=("계약 금액",)))

    assert retriever.search_for_slot.await_count == 3
    composer.assert_not_awaited()
    assert result.success is False
    assert "제한된 답변" in result.text
    assert "999" not in result.text


@pytest.mark.asyncio
async def test_empty_required_claims_fail_closed_without_composer() -> None:
    retriever = AsyncMock()
    composer = AsyncMock(return_value="근거 없는 최종 답변")
    workflow = ComplexFactWorkflow(
        retriever=retriever,
        compose_answer=composer,
        config=ComplexFactWorkflowConfig(max_iterations=3),
    )

    result = await workflow.run_turn_plan(_plan(claims=()))

    assert result.success is False
    assert result.limitations == ["missing_required_claims"]
    assert "required_claims" in result.text
    retriever.search_for_slot.assert_not_awaited()
    composer.assert_not_awaited()


@pytest.mark.asyncio
async def test_filled_required_claims_compose_from_standalone_question() -> None:
    retriever = AsyncMock()
    retriever.search_for_slot.return_value = [
        EvidenceItem(
            source_url="https://official.example/announcement",
            source_type="official",
            claim="official announcement",
            coverage=EvidenceCoverage.FINAL,
            confidence="high",
        )
    ]
    composer = AsyncMock(return_value="공식 발표 기준 제한 없는 답변")
    workflow = ComplexFactWorkflow(
        retriever=retriever,
        compose_answer=composer,
        config=ComplexFactWorkflowConfig(max_iterations=4),
    )

    result = await workflow.run_turn_plan(_plan(claims=("발표 여부",)))

    assert result.success is True
    composer.assert_awaited_once()
    assert composer.await_args.args[0] == _plan().context.standalone_question


@pytest.mark.asyncio
async def test_retrieval_policy_uses_plan_query_and_allowed_collector(monkeypatch) -> None:
    search = AsyncMock(
        return_value=(
            "1. Official announcement\n"
            "URL: https://official.example/announcement\n"
            "Snippet: confirmed today"
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.evidence_retrieval.handle_web_search",
        search,
    )
    retriever = EvidenceRetriever.from_turn_plan(_plan())

    await retriever.search_for_slot("claim_1", "발표 여부 official latest")

    query = search.await_args.args[0]["query"]
    assert "corporate_disclosure" in query
    assert "SK NVIDIA official announcement" in query
    assert "발표 여부" in query

    blocked = EvidenceRetriever.from_turn_plan(_plan(allowed_tools=()))
    assert await blocked.search_for_slot("claim_1", "must not run") == []
    assert search.await_count == 1
