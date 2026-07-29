"""Offline integration for UnifiedTurnPlanner complex-fact ownership."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

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
from simpleclaw.llm.models import LLMResponse


def _config(tmp_path, *, mode: str = "primary", sample_rate: float = 0.0):
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
    mode: {mode}
    sample_rate: {sample_rate}
  complex_fact_workflow:
    source_claim_hardening_ready: true
    max_iterations: 3
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


def _plan(*, fingerprint: str, selected_ids: tuple[str, ...]) -> UnifiedTurnPlan:
    relation = (
        ContextRelation.SAME_THREAD
        if selected_ids
        else ContextRelation.STANDALONE
    )
    return UnifiedTurnPlan(
        original_text="SK와 NVIDIA 협력 발표를 확인해줘",
        context=ContextSelection(
            relation=relation,
            use_prior_context=bool(selected_ids),
            selected_turn_ids=selected_ids,
            standalone_question="SK와 NVIDIA의 공식 협력 발표 여부를 확인해줘",
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
            required_claims=("공식 협력 발표 여부",),
            freshness_required=True,
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.COMPLEX_FACT,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=("web_search",),
            requires_confirmation=False,
            reason="verify official announcement",
        ),
        confidence=0.96,
        decision_summary="complex corporate fact",
        catalog_fingerprint=fingerprint,
    )


def _direct_plan(*, fingerprint: str) -> UnifiedTurnPlan:
    """Phase 2 canary에서 허용되는 asset 없는 direct answer plan을 만든다."""
    return UnifiedTurnPlan(
        original_text="정적 설명을 해줘",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="정적 설명을 해줘",
        ),
        clarification=ClarificationPlan(required=False),
        domains=(),
        intents=("explain",),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="none",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=(),
            requires_confirmation=False,
            reason="static answer",
        ),
        confidence=0.96,
        decision_summary="direct answer",
        catalog_fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_sk_nvidia_initial_and_followup_use_one_plan_per_turn(
    tmp_path,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    planner_calls = 0

    async def fake_planner(_text, *, candidates, catalog, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        selected_ids = (
            (candidates.candidates[-1].turn_id,)
            if planner_calls > 1 and candidates.candidates
            else ()
        )
        return _plan(
            fingerprint=catalog.fingerprint,
            selected_ids=selected_ids,
        )

    async def fake_search(_args, body_fetcher=None):
        return (
            "1. Official announcement\n"
            "URL: https://official.example/sk-nvidia\n"
            "Snippet: confirmed today by the official newsroom"
        )

    async def fake_send(_request):
        return LLMResponse(
            text="공식 뉴스룸 발표를 확인했습니다.",
            model="test",
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.evidence_retrieval.handle_web_search",
        fake_search,
    )
    orchestrator._router.send = fake_send
    deltas: list[str] = []

    async def on_text_delta(delta: str) -> None:
        deltas.append(delta)

    initial = await orchestrator.process_message(
        "SK와 NVIDIA 협력 발표를 확인해줘",
        1,
        1,
        on_text_delta=on_text_delta,
    )
    followup = await orchestrator.process_message(
        "그 발표를 공식 근거로 다시 확인해줘",
        1,
        1,
        on_text_delta=on_text_delta,
    )

    assert planner_calls == 2
    assert initial == "공식 뉴스룸 발표를 확인했습니다."
    assert followup == initial
    assert "2조원" not in initial + followup
    assert deltas == []


@pytest.mark.asyncio
async def test_canary_direct_answer_runs_plan_gate_and_primary_execution(
    tmp_path,
    monkeypatch,
) -> None:
    """100% test cohort의 eligible direct plan이 legacy selector 없이 실행된다."""
    orchestrator = AgentOrchestrator(
        _config(tmp_path, mode="canary", sample_rate=1.0)
    )
    planner_calls = 0

    async def fake_planner(_text, *, catalog, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return _direct_plan(fingerprint=catalog.fingerprint)

    async def fake_send(_request):
        return LLMResponse(text="정적 설명입니다.", model="test")

    async def fail_legacy(*_args, **_kwargs):
        raise AssertionError("legacy semantic path called")

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        fail_legacy,
    )
    orchestrator._router.send = fake_send

    result = await orchestrator.process_message(
        "정적 설명을 해줘",
        user_id=1,
        chat_id=1,
    )

    assert result == "정적 설명입니다."
    assert planner_calls == 1


@pytest.mark.asyncio
async def test_required_fact_plan_collects_before_no_tool_final(
    tmp_path,
    monkeypatch,
) -> None:
    """fact_check.required가 collector와 finalization gate까지 연결된다."""

    orchestrator = AgentOrchestrator(_config(tmp_path))

    async def fake_planner(_text, *, catalog, **_kwargs):
        plan = _plan(fingerprint=catalog.fingerprint, selected_ids=())
        return replace(
            plan,
            context=replace(
                plan.context,
                standalone_question='"이런 엿같은 사랑" Netflix 등장인물을 확인해줘',
            ),
            domains=("entertainment",),
            intents=("drama_info",),
            fact_check=replace(
                plan.fact_check,
                domain="entertainment",
                    entities=("이런 엿같은 사랑", "Netflix", "하영", "정해영"),
                    search_query='"이런 엿같은 사랑" Netflix 하영 정해영 등장인물',
                    required_claims=("하영", "정해영"),
                    freshness_required=False,
            ),
            execution=replace(
                plan.execution,
                mode=ExecutionMode.FACT_CHECK,
            ),
        )

    search = AsyncMock(
        return_value=(
                "WEB_SEARCH_RESULTS: drama (1 results)\n"
                '1. "이런 엿같은 사랑" Netflix cast page\n'
                "URL: https://www.netflix.com/example\n"
                "Snippet: 하영과 정해영 cast metadata"
            )
        )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_web_search",
        search,
    )

    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        return LLMResponse(text="검증된 등장인물 답변", model="test")

    orchestrator._router.send = fake_send
    result = await orchestrator.process_message(
        '"이런 엿같은 사랑"이라는 드라마 등장인물 찾아줘.',
        user_id=1,
        chat_id=1,
    )

    assert result == "검증된 등장인물 답변"
    search.assert_awaited_once()
    assert "이런 엿같은 사랑" in search.call_args.args[0]["query"]
    assert len(seen_requests) == 1
    assert "Validated Current-Turn Evidence" not in seen_requests[0].system_prompt
    assert any(
        "https://www.netflix.com/example" in message.get("content", "")
        and message.get("_evidence_context") is True
        for message in seen_requests[0].messages
    )
