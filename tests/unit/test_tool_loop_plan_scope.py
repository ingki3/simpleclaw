"""BIZ-495 — Unified plan이 고정한 context/tool scope의 실행 계약."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.tool_dispatch import dispatch_tool_call
from simpleclaw.agent.tool_gate import ToolExecutionScope
from simpleclaw.agent.tool_loop import ToolLoopRunner
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
from simpleclaw.agent.turn_planner import plan_turn_with_llm
from simpleclaw.capability import CapabilityMetadata
from simpleclaw.llm.models import LLMResponse, ToolCall
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.skills.models import SkillDefinition


@pytest.fixture
def primary_config(tmp_path):
    """primary planner와 의도적으로 켠 legacy selector를 함께 구성한다."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-2.0-flash
      api_key: test-key
agent:
  history_limit: 8
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 3
  unified_turn_planner:
    mode: primary
    context_candidate_limit: 8
    context_candidate_max_chars: 6000
    selected_context_max_turns: 3
    selected_context_max_chars: 2400
  turn_analysis:
    enabled: true
asset_selection:
  enabled: true
  bypass_below_count: 0
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
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text(
        "# Agent\nYou are SimpleClaw.",
        encoding="utf-8",
    )
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


def _candidate_set() -> ContextCandidateSet:
    candidates = (
        ContextCandidate(
            turn_id="msg:11",
            role="user",
            timestamp=datetime.now(UTC),
            content="롯데 경기 결과를 확인해줘",
            trust=ContextTrust.USER_INPUT,
        ),
        ContextCandidate(
            turn_id="msg:12",
            role="assistant",
            timestamp=datetime.now(UTC),
            content="과거 답변은 최신 근거가 아닙니다",
            trust=ContextTrust.ASSISTANT_CONTEXT_ONLY,
        ),
        ContextCandidate(
            turn_id="msg:13",
            role="user",
            timestamp=datetime.now(UTC),
            content="완전히 다른 주제",
            trust=ContextTrust.USER_INPUT,
        ),
    )
    return ContextCandidateSet(
        candidates=candidates,
        total_chars=sum(len(item.content) for item in candidates),
        truncated=False,
    )


def _plan(
    *,
    fingerprint: str,
    relation: ContextRelation = ContextRelation.STANDALONE,
    selected_turn_ids: tuple[str, ...] = (),
    standalone_question: str = "독립 질문",
    mode: ExecutionMode = ExecutionMode.DIRECT_ANSWER,
    primary_asset: AssetRef | None = None,
    allowed_assets: tuple[AssetRef, ...] = (),
    allowed_tools: tuple[str, ...] = (),
    fact_required: bool = False,
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="원문",
        context=ContextSelection(
            relation=relation,
            use_prior_context=bool(selected_turn_ids),
            selected_turn_ids=selected_turn_ids,
            standalone_question=standalone_question,
        ),
        clarification=ClarificationPlan(required=False),
        domains=("sports",) if fact_required else (),
        intents=("current_result",) if fact_required else (),
        fact_check=FactCheckPlan(
            required=fact_required,
            owner=EvidenceOwner.PLANNER if fact_required else EvidenceOwner.NONE,
            domain="sports" if fact_required else "none",
            entities=("롯데",) if fact_required else (),
            search_query="롯데 오늘 경기 결과" if fact_required else "",
            required_claims=("최종 점수",) if fact_required else (),
            freshness_required=fact_required,
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=primary_asset,
            allowed_assets=allowed_assets,
            allowed_tools=allowed_tools,
            requires_confirmation=False,
            reason="test",
        ),
        confidence=0.95,
        decision_summary="test",
        catalog_fingerprint=fingerprint,
    )


def _safe_skill(name: str) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        description=f"{name} lookup",
        capability=CapabilityMetadata(
            domains=("sports",),
            intents=("current_result",),
            read_only=True,
            side_effects=False,
            direct_answer=True,
            declared=True,
        ),
    )


@pytest.mark.asyncio
async def test_primary_planner_runs_once_and_only_selected_history_reaches_loop(
    primary_config,
    monkeypatch,
):
    """Planner 선택은 iteration 밖에서 한 번 고정되고 selector/history를 대체한다."""
    orchestrator = AgentOrchestrator(primary_config)
    orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.USER, content="선택할 사용자 문맥")
    )
    selected_row_id = orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.ASSISTANT, content="선택할 assistant 문맥")
    )
    orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.USER, content="선택하면 안 되는 문맥")
    )

    planner_calls = 0

    async def fake_planner(_text, *, candidates, catalog, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return _plan(
            fingerprint=catalog.fingerprint,
            relation=ContextRelation.SAME_THREAD,
            selected_turn_ids=(f"msg:{selected_row_id}",),
            standalone_question="정규화된 독립 질문",
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    analyzer = AsyncMock(side_effect=AssertionError("legacy analyzer called"))
    selector = AsyncMock(side_effect=AssertionError("legacy selector called"))
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        analyzer,
    )
    monkeypatch.setattr(orchestrator, "_select_assets_for_turn", selector)

    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        return LLMResponse(text="최종 답변", model="test")

    orchestrator._router.send = fake_send

    result = await orchestrator.process_message("그건 지금 어때?", 1, 1)

    assert result == "최종 답변"
    assert planner_calls == 1
    analyzer.assert_not_awaited()
    selector.assert_not_awaited()
    assert len(seen_requests) == 1
    messages = seen_requests[0].messages
    assert [message["role"] for message in messages] == ["assistant", "user"]
    assert messages[0]["content"] == "선택할 assistant 문맥"
    assert "정규화된 독립 질문" in messages[1]["content"]
    assert all("선택하면 안 되는 문맥" not in message["content"] for message in messages)


@pytest.mark.asyncio
async def test_standalone_plan_has_zero_prior_messages_and_disables_rag(
    primary_config,
    monkeypatch,
):
    """standalone/topic-shift는 자동 history/RAG를 다시 끌어오지 않는다."""
    orchestrator = AgentOrchestrator(primary_config)
    candidates = _candidate_set()
    selector = AsyncMock(side_effect=AssertionError("selector called"))
    retrieve = AsyncMock(side_effect=AssertionError("RAG called"))
    monkeypatch.setattr(orchestrator, "_select_assets_for_turn", selector)
    monkeypatch.setattr(orchestrator, "_retrieve_relevant_context", retrieve)

    state = await orchestrator._prepare_tool_loop_state(
        "원문",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=_plan(
            fingerprint="unused",
            relation=ContextRelation.TOPIC_SHIFT,
            standalone_question="새 주제 질문",
        ),
        candidates=candidates,
    )

    assert state.selected_turn_ids == ()
    assert len(state.messages) == 1
    assert state.messages[0]["role"] == "user"
    assert "새 주제 질문" in state.messages[0]["content"]
    assert all(
        candidate.content not in state.messages[0]["content"]
        for candidate in candidates.candidates
    )
    selector.assert_not_awaited()
    retrieve.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_asset_exposes_only_exact_primary_skill(primary_config):
    """safe direct asset는 다른 allowlisted 후보까지 노출하지 않는 좁은 loop다."""
    orchestrator = AgentOrchestrator(primary_config)
    alpha = _safe_skill("alpha-skill")
    beta = _safe_skill("beta-skill")
    orchestrator._skills = [alpha, beta]
    orchestrator._skills_by_name = {skill.name: skill for skill in (alpha, beta)}

    alpha_ref = AssetRef("skill", "alpha-skill")
    state = await orchestrator._prepare_tool_loop_state(
        "원문",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=_plan(
            fingerprint="unused",
            mode=ExecutionMode.EXECUTE_ASSET,
            primary_asset=alpha_ref,
            allowed_assets=(alpha_ref, AssetRef("skill", "beta-skill")),
            allowed_tools=("execute_skill",),
        ),
        candidates=_candidate_set(),
    )

    assert [tool.name for tool in state.tools] == ["execute_skill"]
    assert "alpha-skill" in state.system_prompt
    assert "beta-skill" not in state.system_prompt
    assert state.execution_scope is not None
    assert state.execution_scope.allowed_assets == frozenset(
        {("skill", "alpha-skill")}
    )


@pytest.mark.asyncio
async def test_raw_planner_boundary_executes_exact_skill_from_production_catalog(
    primary_config,
    monkeypatch,
):
    """production catalog의 raw plan이 경계를 통과해 exact skill만 실행한다."""
    orchestrator = AgentOrchestrator(primary_config)
    safe_skill = _safe_skill("safe-skill")
    orchestrator._skills = [safe_skill]
    orchestrator._skills_by_name = {safe_skill.name: safe_skill}
    catalog = build_planner_catalog(skills=[safe_skill], native_specs=[])
    planner_payload = {
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": "safe skill로 조회해줘",
            "unresolved_references": [],
            "ignored_context_reason": "독립 요청",
        },
        "clarification": {
            "required": False,
            "question": "",
            "options": [],
            "reason": "",
        },
        "domains": ["sports"],
        "intents": ["current_result"],
        "fact_check": {
            "required": False,
            "owner": "none",
            "domain": "none",
            "entities": [],
            "search_query": "",
            "required_claims": [],
            "freshness_required": False,
            "reason": "",
        },
        "execution": {
            "mode": "execute_asset",
            "primary_asset": {
                "asset_type": "skill",
                "asset_name": "safe-skill",
            },
            "allowed_assets": [
                {
                    "asset_type": "skill",
                    "asset_name": "safe-skill",
                }
            ],
            "allowed_tools": ["execute_skill"],
            "requires_confirmation": False,
            "reason": "read-only exact skill",
        },
        "confidence": 0.96,
        "decision_summary": "safe skill 하나만 실행한다.",
    }
    planner_router = AsyncMock()
    planner_router.send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(planner_payload, ensure_ascii=False),
            model="test",
        )
    )

    plan = await plan_turn_with_llm(
        "safe skill로 조회해줘",
        candidates=_candidate_set(),
        catalog=catalog,
        router=planner_router,
    )
    state = await orchestrator._prepare_tool_loop_state(
        "safe skill로 조회해줘",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=plan,
        candidates=_candidate_set(),
    )
    execute_registered = AsyncMock(return_value="safe skill result")
    monkeypatch.setattr(
        "simpleclaw.agent.skill_dispatch.execute_registered_skill",
        execute_registered,
    )
    responses = [
        LLMResponse(
            text="",
            model="test",
            tool_calls=[
                ToolCall(
                    id="safe-call",
                    name="execute_skill",
                    arguments={"skill_name": "safe-skill"},
                )
            ],
        ),
        LLMResponse(text="safe skill 결과입니다", model="test"),
    ]

    async def fake_send(_request):
        return responses.pop(0)

    orchestrator._router.send = fake_send
    result = await ToolLoopRunner(orchestrator).run(state)

    assert result.text == "safe skill 결과입니다"
    assert [tool.name for tool in state.tools] == ["execute_skill"]
    execute_registered.assert_awaited_once_with(
        orchestrator,
        "safe-skill",
        "",
        exact=True,
    )


@pytest.mark.asyncio
async def test_tool_gate_returns_structured_observation_before_dispatch():
    """allowlist 밖 native call은 handler를 실행하지 않고 stable code로 관찰된다."""
    orchestrator = AsyncMock()
    scope = ToolExecutionScope(
        allowed_tools=frozenset({"web_fetch"}),
        allowed_assets=frozenset(),
        operator_tools=False,
        allow_cron_mutation=False,
    )

    result = await dispatch_tool_call(
        orchestrator,
        ToolCall(id="blocked", name="cli", arguments={"command": "pwd"}),
        execution_scope=scope,
    )

    payload = json.loads(result)
    assert payload["error"]["code"] == "tool_not_allowed"
    orchestrator._execute_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_planned_skill_name_is_exact_not_fuzzy():
    """legacy fuzzy 별칭이 exact planned skill allowlist를 우회하지 못한다."""
    orchestrator = AsyncMock()
    scope = ToolExecutionScope(
        allowed_tools=frozenset({"execute_skill"}),
        allowed_assets=frozenset({("skill", "weather-skill")}),
        operator_tools=False,
        allow_cron_mutation=False,
    )

    result = await dispatch_tool_call(
        orchestrator,
        ToolCall(
            id="blocked",
            name="execute_skill",
            arguments={"skill_name": "weather"},
        ),
        execution_scope=scope,
    )

    payload = json.loads(result)
    assert payload["error"]["code"] == "skill_not_allowed"
    orchestrator._dispatch_external_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_keeps_blocked_call_as_observation_without_execution(
    primary_config,
    monkeypatch,
):
    """각 iteration의 hallucinated call도 gate를 지나며 loop는 관찰 후 계속된다."""
    orchestrator = AgentOrchestrator(primary_config)
    execute = AsyncMock(side_effect=AssertionError("blocked cli executed"))
    monkeypatch.setattr(orchestrator, "_execute_command", execute)
    responses = [
        LLMResponse(
            text="",
            model="test",
            tool_calls=[
                ToolCall(
                    id="blocked",
                    name="cli",
                    arguments={"command": "pwd"},
                )
            ],
        ),
        LLMResponse(text="차단 사실을 반영한 답변", model="test"),
    ]
    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        return responses.pop(0)

    orchestrator._router.send = fake_send
    plan = _plan(
        fingerprint="unused",
        allowed_assets=(AssetRef("native_tool", "web_fetch"),),
        allowed_tools=("web_fetch",),
    )
    state = await orchestrator._prepare_tool_loop_state(
        "질문",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=plan,
        candidates=_candidate_set(),
    )

    result = await ToolLoopRunner(orchestrator).run(state)

    assert result.text == "차단 사실을 반영한 답변"
    execute.assert_not_awaited()
    observation = next(
        message
        for message in seen_requests[1].messages
        if message.get("tool_call_id") == "blocked"
    )
    structured = observation["content"].split("\n\n", 1)[0]
    assert json.loads(structured)["error"]["code"] == "tool_not_allowed"


@pytest.mark.asyncio
async def test_fact_check_plan_creates_common_evidence_gate_state(
    primary_config,
):
    """BIZ-520: fact-check plan은 공통 evidence contract로 변환된다."""
    orchestrator = AgentOrchestrator(primary_config)
    state = await orchestrator._prepare_tool_loop_state(
        "원문",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=_plan(
            fingerprint="unused",
            relation=ContextRelation.SAME_THREAD,
            selected_turn_ids=("msg:12",),
            standalone_question="롯데 오늘 경기 결과",
            mode=ExecutionMode.FACT_CHECK,
            allowed_assets=(AssetRef("native_tool", "web_fetch"),),
            allowed_tools=("web_fetch",),
            fact_required=True,
        ),
        candidates=_candidate_set(),
    )

    assert state.messages[0]["role"] == "assistant"
    assert state.evidence_requirement.required is True
    assert state.evidence_requirement.query == "롯데 오늘 경기 결과"
    assert state.evidence_requirement.allowed_collectors == frozenset({"web_fetch"})
    assert state.evidence_state is not None
    assert state.evidence_state.status.value == "not_searched"
    assert state.execution_scope is not None
    assert state.execution_scope.allowed_tools == frozenset({"web_fetch"})


@pytest.mark.asyncio
async def test_fact_check_plan_still_executes_web_search(
    primary_config,
    monkeypatch,
):
    """planner fact-check는 허용된 조회 도구를 한 번만 실행해 근거를 재사용한다."""
    orchestrator = AgentOrchestrator(primary_config)
    search = AsyncMock(
        return_value=(
            "WEB_SEARCH_RESULTS: '롯데 경기 결과' (1 results)\n"
            "1. 공식 경기 결과\n"
            "   URL: https://example.com/official-result\n"
            "   Snippet: 롯데 최종 점수는 3대 2로 승리했습니다."
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_web_search",
        search,
    )
    plan = _plan(
        fingerprint="unused",
        standalone_question="롯데 오늘 경기 결과",
        mode=ExecutionMode.FACT_CHECK,
        allowed_assets=(AssetRef("native_tool", "web_search"),),
        allowed_tools=("web_search",),
        fact_required=True,
    )
    state = await orchestrator._prepare_tool_loop_state(
        "롯데 오늘 경기 결과",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=plan,
        candidates=_candidate_set(),
    )
    responses = [
        LLMResponse(
            text="",
            model="test",
            tool_calls=[
                ToolCall(
                    id="search-call",
                    name="web_search",
                    arguments={"query": "롯데 오늘 경기 결과"},
                )
            ],
        ),
        LLMResponse(text="공식 결과를 확인했습니다", model="test"),
    ]

    async def fake_send(_request):
        return responses.pop(0)

    orchestrator._router.send = fake_send
    result = await ToolLoopRunner(orchestrator).run(state)

    assert result.text == "공식 결과를 확인했습니다"
    search.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search_result",
    [
        "",
        "Error: web search backend unavailable",
    ],
)
async def test_web_search_empty_or_error_blocks_unverified_final(
    primary_config,
    monkeypatch,
    search_result,
):
    """empty와 failed를 구분하고 근거 없는 LLM final을 실행하지 않는다."""
    orchestrator = AgentOrchestrator(primary_config)
    search = AsyncMock(return_value=search_result)
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_web_search",
        search,
    )
    plan = _plan(
        fingerprint="unused",
        standalone_question="롯데 오늘 경기 결과",
        mode=ExecutionMode.FACT_CHECK,
        allowed_assets=(AssetRef("native_tool", "web_search"),),
        allowed_tools=("web_search",),
        fact_required=True,
    )
    state = await orchestrator._prepare_tool_loop_state(
        "롯데 오늘 경기 결과",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=plan,
        candidates=_candidate_set(),
    )
    orchestrator._router.send = AsyncMock(
        return_value=LLMResponse(text="근거 없이 단정한 결과", model="test")
    )
    result = await ToolLoopRunner(orchestrator).run(state)

    assert "근거 없이 단정한 결과" not in result.text
    assert "찾지 못했습니다" in result.text or "조회가 실패" in result.text
    search.assert_awaited_once()
    orchestrator._router.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_fact_required_primary_path_suppresses_streaming_but_preserves_final(
    primary_config,
    monkeypatch,
):
    """fact-check 조회 경로는 streaming을 억제해도 생성된 final은 보존한다."""
    orchestrator = AgentOrchestrator(primary_config)
    search = AsyncMock(
        return_value=(
            "WEB_SEARCH_RESULTS: query (1 results)\n"
            "1. 롯데 최종 점수 공식 경기 결과\n"
            "URL: https://example.com/official-result"
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_web_search",
        search,
    )

    async def fake_planner(_text, *, catalog, **_kwargs):
        return _plan(
            fingerprint=catalog.fingerprint,
            standalone_question="롯데 오늘 경기 결과",
            mode=ExecutionMode.FACT_CHECK,
            allowed_assets=(AssetRef("native_tool", "web_search"),),
            allowed_tools=("web_search",),
            fact_required=True,
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    send_callbacks = []

    async def fake_send(_request, on_text_delta=None):
        send_callbacks.append(on_text_delta)
        return LLMResponse(text="근거 없는 점수", model="test")

    orchestrator._router.send = fake_send
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    result = await orchestrator.process_message(
        "롯데 오늘 경기 결과",
        1,
        1,
        on_text_delta=on_delta,
    )

    assert send_callbacks == [None]
    assert deltas == []
    assert result == "근거 없는 점수"
    search.assert_awaited_once()
