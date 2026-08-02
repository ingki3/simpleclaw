"""BIZ-523 ordinary primary controller integration tests."""

from __future__ import annotations

import json

import pytest

from simpleclaw.agent.orchestrator import (
    _UNIFIED_PLAN_UNAVAILABLE_MESSAGE,
    AgentOrchestrator,
)
from simpleclaw.agent.session_state import SessionIdentity
from simpleclaw.agent.turn_plan import (
    AssetRef,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    FactEntity,
    UnifiedTurnPlan,
)
from simpleclaw.agent.turn_planner import PlannerUnavailable
from simpleclaw.llm.models import LLMResponse
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def config_file(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-test
      api_key: test-key
agent:
  db_path: "{tmp_path}/conversations.db"
  history_limit: 8
  max_tool_iterations: 2
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
  local_dir: "{tmp_path}/persona"
  global_dir: "{tmp_path}/global_persona"
  files: []
memory:
  rag:
    enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    (tmp_path / "persona").mkdir()
    (tmp_path / "global_persona").mkdir()
    return config


def _plan(
    text: str,
    fingerprint: str,
    *,
    mode: ExecutionMode = ExecutionMode.DIRECT_ANSWER,
    relation: ContextRelation = ContextRelation.STANDALONE,
    clarification: ClarificationPlan | None = None,
    fact: bool = False,
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text=text,
        context=ContextSelection(
            relation=relation,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question=text,
        ),
        clarification=clarification or ClarificationPlan(required=False),
        domains=("sports",) if fact else (),
        intents=("current_result",) if fact else (),
        fact_check=FactCheckPlan(
            required=fact,
            owner=EvidenceOwner.PLANNER if fact else EvidenceOwner.NONE,
            domain="sports" if fact else "",
            intents=("current_result",) if fact else (),
            entities=(
                (FactEntity(kind="league", value="LPGA"),)
                if fact
                else ()
            ),
            reference_date="2026-07-31" if fact else "",
            search_query=text if fact else "",
            required_claims=("official current result",) if fact else (),
            freshness_required=fact,
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=None,
            allowed_assets=(
                (AssetRef("native_tool", "web_search"),)
                if fact
                else ()
            ),
            allowed_tools=("web_search",) if fact else (),
            requires_confirmation=False,
            reason="test",
        ),
        confidence=0.95,
        decision_summary="test",
        catalog_fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_process_message_uses_one_primary_plan_and_scoped_store(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    calls = 0

    async def planner(text, *, catalog, **_kwargs):
        nonlocal calls
        calls += 1
        return _plan(text, catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    orchestrator._router.send = lambda _request: _async_response("typed answer")

    result = await orchestrator.process_message("hello", 10, 20)

    assert result == "typed answer"
    assert calls == 1
    session_key = SessionIdentity("telegram", "10", "20").stable_key()
    assert [
        message.content
        for message in orchestrator._store.get_recent(
            session_key=session_key
        )
    ] == ["hello", "typed answer"]


async def _async_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="test")


@pytest.mark.asyncio
async def test_planner_unavailable_fails_closed_without_tool_execution(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)

    async def unavailable(*_args, **_kwargs):
        raise PlannerUnavailable("unavailable")

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        unavailable,
    )
    called = False

    async def dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return "should not run"

    orchestrator._dispatch_tool_call = dispatch
    assert (
        await orchestrator.process_message("do something", 10, 20)
        == _UNIFIED_PLAN_UNAVAILABLE_MESSAGE
    )
    assert called is False


@pytest.mark.asyncio
async def test_unsupported_fact_lookup_never_enters_composer(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)

    async def planner(text, *, catalog, **_kwargs):
        return _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.FACT_CHECK,
            fact=True,
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )

    async def unsupported(*_args, **_kwargs):
        return json.dumps(
            {
                "lookup_status": "unsupported",
                "evidence": [],
                "facts": [],
                "limitations": ["LPGA adapter unavailable"],
            }
        )

    orchestrator._execute_skill = unsupported
    composer_called = False

    async def composer(*_args, **_kwargs):
        nonlocal composer_called
        composer_called = True
        return LLMResponse(text="invented result", model="test")

    orchestrator._router.send = composer
    result = await orchestrator.process_message(
        "LPGA current result",
        10,
        20,
    )
    assert "확정할 수 없습니다" in result
    assert composer_called is False


@pytest.mark.asyncio
async def test_found_low_confidence_fact_returns_limited_final(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)

    async def planner(text, *, catalog, **_kwargs):
        return _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.FACT_CHECK,
            fact=True,
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )

    async def low_confidence_found(*_args, **_kwargs):
        return json.dumps(
            {
                "lookup_status": "found",
                "kind": "sports",
                "confidence": "low",
                "facts": [
                    {
                        "claim": "LPGA final score 68 and win",
                        "source_url": "https://example.test/lpga",
                    }
                ],
                "timeline_validation": {"status": "final"},
                "limitations": ["low confidence"],
            }
        )

    orchestrator._execute_skill = low_confidence_found
    composer_called = False

    async def composer(*_args, **_kwargs):
        nonlocal composer_called
        composer_called = True
        return LLMResponse(text="invented 68 and win", model="test")

    orchestrator._router.send = composer

    result = await orchestrator.process_message("LPGA current result", 10, 20)

    assert "확정할 수 없습니다" in result
    assert "LPGA final score 68 and win" not in result
    assert "https://example.test/lpga" not in result
    assert composer_called is False


@pytest.mark.asyncio
async def test_context_candidates_do_not_cross_session(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    session_a = SessionIdentity("telegram", "10", "20").stable_key()
    session_b = SessionIdentity("telegram", "11", "20").stable_key()
    orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.USER, content="A context"),
        session_key=session_a,
    )
    orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.USER, content="B secret"),
        session_key=session_b,
    )

    async def planner(text, *, candidates, catalog, **_kwargs):
        contents = [item.content for item in candidates.candidates]
        assert "A context" in contents
        assert "B secret" not in contents
        return _plan(text, catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    orchestrator._router.send = lambda _request: _async_response("ok")
    assert await orchestrator.process_message("next", 10, 20) == "ok"


@pytest.mark.asyncio
async def test_clarification_survives_orchestrator_reopen(
    config_file,
    monkeypatch,
) -> None:
    first = AgentOrchestrator(config_file)

    async def planner(text, *, catalog, **_kwargs):
        return _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.CLARIFY,
            relation=ContextRelation.UNCLEAR,
            clarification=ClarificationPlan(
                required=True,
                question="Which league?",
                options=("KBO", "LPGA"),
            ),
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    assert "Which league?" in await first.process_message("that result", 10, 20)

    reopened = AgentOrchestrator(config_file)
    pending = reopened.get_pending_clarify(10, 20)
    assert pending is not None
    assert [option.body for option in pending.options] == ["KBO", "LPGA"]
