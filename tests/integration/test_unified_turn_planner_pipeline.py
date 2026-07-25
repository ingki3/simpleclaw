"""Offline integration for UnifiedTurnPlanner complex-fact ownership."""

from __future__ import annotations

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


def _config(tmp_path):
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
