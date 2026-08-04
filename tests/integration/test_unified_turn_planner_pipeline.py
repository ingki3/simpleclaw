"""Offline integration for UnifiedTurnPlanner complex-fact ownership."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.resolution_types import ComplexitySignal
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
            intents=("current_fact",),
            required_claims=("공식 협력 발표 여부",),
            freshness_required=True,
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=("web_search",),
            requires_confirmation=False,
            reason="verify official announcement",
            complexity_signals=(
                ComplexitySignal.ORDERED_CAPABILITY_COMPOSITION,
            ),
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


def _lookup_result(*, domain: str, claim: str, source_url: str) -> str:
    """실시간 조회 대역이 현행 typed evidence 경계를 통과하도록 결과를 직렬화한다."""

    return json.dumps(
        {
            "kind": domain,
            "lookup_status": "found",
            "confidence": "high",
            "evidence": [{"source_url": source_url, "snippet": claim}],
            "facts": [{"claim": claim, "source_url": source_url}],
            "timeline_validation": {"status": "final"},
            "limitations": [],
        }
    )


def _lookup_payload(token: str) -> dict:
    """조회 요청의 도메인과 질의를 검증할 수 있도록 인코딩된 payload를 복원한다."""

    return json.loads(base64.urlsafe_b64decode(token).decode("utf-8"))


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

    async def fake_lookup(_skill_name, _token):
        return _lookup_result(
            domain="corporate_disclosure",
            claim="SK NVIDIA 공식 협력 발표 여부: 공식 뉴스룸에서 확정",
            source_url="https://official.example/sk-nvidia",
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
    orchestrator._execute_skill = fake_lookup
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

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
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
async def test_two_turn_drama_followup_preserves_context_in_collector_query(
    tmp_path,
    monkeypatch,
) -> None:
    """실제 2턴 후보에서 제목·플랫폼·서로 다른 인물 단서를 보존한다."""

    orchestrator = AgentOrchestrator(_config(tmp_path))
    planner_inputs = []

    async def fake_planner(text, *, candidates, catalog, **_kwargs):
        planner_inputs.append((text, candidates))
        initial_candidates = [
            candidate
            for candidate in candidates.candidates
            if candidate.role == "user"
            and "이런 엿같은 사랑" in candidate.content
        ]
        if not initial_candidates:
            selected_ids = ()
            standalone_question = (
                '"이런 엿같은 사랑"에서 정해영이 맡은 등장인물을 찾아줘'
            )
            entities = ("이런 엿같은 사랑", "정해영")
            search_query = '"이런 엿같은 사랑" 정해영 등장인물'
            required_claims = ("정해영",)
        else:
            assert len(initial_candidates) == 1
            initial_candidate = initial_candidates[0]
            assert "정해영" in initial_candidate.content
            assert "Netflix" in text
            assert "하영" in text
            selected_ids = (initial_candidate.turn_id,)
            standalone_question = (
                '"이런 엿같은 사랑" Netflix 드라마에서 하영과 '
                "정해영 등장인물을 찾아줘"
            )
            entities = ("이런 엿같은 사랑", "Netflix", "하영", "정해영")
            search_query = '"이런 엿같은 사랑" Netflix 하영 정해영 등장인물'
            required_claims = ("하영", "정해영")

        plan = _plan(
            fingerprint=catalog.fingerprint,
            selected_ids=selected_ids,
        )
        return replace(
            plan,
            original_text=text,
            context=replace(
                plan.context,
                standalone_question=standalone_question,
            ),
            domains=("entertainment",),
            intents=("drama_info",),
            fact_check=replace(
                plan.fact_check,
                domain="entertainment",
                entities=entities,
                search_query=search_query,
                intents=("drama_info",),
                required_claims=required_claims,
                freshness_required=False,
            ),
            execution=replace(
                plan.execution,
                mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            ),
        )

    lookup = AsyncMock(
        return_value=_lookup_result(
            domain="entertainment",
            claim='"이런 엿같은 사랑" Netflix 하영 정해영 등장인물',
            source_url="https://www.netflix.com/example",
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    orchestrator._execute_skill = lookup

    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        return LLMResponse(text="검증된 등장인물 답변", model="test")

    orchestrator._router.send = fake_send
    initial = await orchestrator.process_message(
        '"이런 엿같은 사랑"에서 정해영이 맡은 등장인물을 찾아줘.',
        user_id=1,
        chat_id=1,
    )
    followup = await orchestrator.process_message(
        "Netflix에서 봤고 하영이 나왔어. 그 작품 등장인물 다시 찾아줘.",
        user_id=1,
        chat_id=1,
    )

    assert initial == "검증된 등장인물 답변"
    assert followup == initial
    assert len(planner_inputs) == 2
    second_candidates = planner_inputs[1][1].candidates
    assert any(
        candidate.role == "user"
        and "이런 엿같은 사랑" in candidate.content
        and "정해영" in candidate.content
        for candidate in second_candidates
    )
    assert lookup.await_count == 2
    final_query = _lookup_payload(lookup.await_args_list[-1].args[1])["query"]
    for clue in ("이런 엿같은 사랑", "Netflix", "하영", "정해영"):
        assert clue in final_query
    assert "하영(정해영)" not in final_query
    assert len(seen_requests) == 2
    assert all(
        "Validated Current-Turn Evidence" not in request.system_prompt
        for request in seen_requests
    )
    assert any(
        "https://www.netflix.com/example" in message.get("content", "")
        and message.get("_evidence_context") is True
        for message in seen_requests[-1].messages
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "primary"])
async def test_player_status_requires_current_turn_evidence_in_configured_modes(
    tmp_path,
    monkeypatch,
    mode,
) -> None:
    """SP-01/SP-06 설정 모드가 현행 unified plan gate를 사용함을 검증한다."""

    query = "롯데 홍민기 요즘 어떤 상태야??"
    orchestrator = AgentOrchestrator(_config(tmp_path, mode=mode))

    async def fake_planner(text, *, catalog, **_kwargs):
        assert text == query
        plan = _plan(fingerprint=catalog.fingerprint, selected_ids=())
        return replace(
            plan,
            original_text=query,
            context=replace(plan.context, standalone_question=query),
            domains=("sports", "sports_news"),
            intents=("realtime_lookup", "player_status"),
            fact_check=replace(
                plan.fact_check,
                domain="sports_news",
                entities=("롯데", "홍민기"),
                search_query=query,
                intents=("realtime_lookup", "player_status"),
                required_claims=("선수 상태",),
                freshness_required=True,
            ),
            execution=replace(
                plan.execution,
                mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            ),
        )

    planner = AsyncMock(side_effect=fake_planner)
    lookup = AsyncMock(
        return_value=_lookup_result(
            domain="sports_news",
            claim="롯데 홍민기 선수 상태 2026-07-30 기준",
            source_url="https://sports.example/players/hong-min-ki",
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    orchestrator._execute_skill = lookup
    orchestrator._router.send = AsyncMock(
        return_value=LLMResponse(
            text="검증된 현재 선수 상태 근거 기반 답변",
            model="test",
        )
    )

    result = await orchestrator.process_message(
        query,
        user_id=1,
        chat_id=1,
    )

    assert result == "검증된 현재 선수 상태 근거 기반 답변"
    lookup.assert_awaited_once()
    assert _lookup_payload(lookup.await_args.args[1])["query"] == query
    planner.assert_awaited_once()
