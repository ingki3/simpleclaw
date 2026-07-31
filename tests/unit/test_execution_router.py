"""BIZ-494 — UnifiedTurnPlan ExecutionRouter의 순수 dispatch 계약."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.execution_router import (
    ExecutionCallbacks,
    ExecutionRouter,
)
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
from simpleclaw.agent.turn_state import TurnExecutionState

_MODE_CALLBACKS = (
    (ExecutionMode.CLARIFY, "clarify"),
    (ExecutionMode.DIRECT_ANSWER, "direct_answer"),
    (ExecutionMode.EXECUTE_ASSET, "execute_asset"),
    (ExecutionMode.TOOL_LOOP, "tool_loop"),
    (ExecutionMode.FACT_CHECK, "fact_check"),
    (ExecutionMode.COMPLEX_FACT, "complex_fact"),
    (ExecutionMode.RECIPE, "recipe"),
)


def _plan(mode: ExecutionMode) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="현재 질문",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="현재 질문",
        ),
        clarification=ClarificationPlan(required=False),
        domains=(),
        intents=(),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=(),
            requires_confirmation=False,
            reason="test",
        ),
        confidence=1.0,
        decision_summary="test plan",
    )


def _state(mode: ExecutionMode) -> TurnExecutionState:
    plan = _plan(mode)
    state = TurnExecutionState.create(
        session_key="telegram:A",
        original_text=plan.original_text,
    )
    state.attach_plan(plan)
    return state


def _callbacks(selected_name: str) -> tuple[ExecutionCallbacks, dict]:
    callbacks = {
        name: AsyncMock(
            side_effect=(
                (lambda state: state)
                if name == selected_name
                else AssertionError(f"unexpected callback: {name}")
            )
        )
        for _mode, name in _MODE_CALLBACKS
    }
    return ExecutionCallbacks(**callbacks), callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "callback_name"), _MODE_CALLBACKS)
async def test_dispatches_each_mode_to_exact_callback(
    mode: ExecutionMode,
    callback_name: str,
) -> None:
    state = _state(mode)
    callbacks, spies = _callbacks(callback_name)

    result = await ExecutionRouter(callbacks).dispatch(state)

    assert result is state
    spies[callback_name].assert_awaited_once_with(state)
    for name, spy in spies.items():
        if name != callback_name:
            spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_result_is_returned_without_transformation() -> None:
    state = _state(ExecutionMode.TOOL_LOOP)
    routed = _state(ExecutionMode.TOOL_LOOP)
    callbacks, _spies = _callbacks("tool_loop")
    callbacks.tool_loop.return_value = routed
    callbacks.tool_loop.side_effect = None

    dispatched = await ExecutionRouter(callbacks).dispatch(state)

    assert dispatched is routed


@pytest.mark.asyncio
async def test_dispatch_does_not_invoke_planner_or_llm(monkeypatch) -> None:
    planner = AsyncMock(side_effect=AssertionError("planner must not run"))
    monkeypatch.setattr(
        "simpleclaw.agent.turn_planner.plan_turn_with_llm",
        planner,
    )
    callbacks, spies = _callbacks("direct_answer")
    state = _state(ExecutionMode.DIRECT_ANSWER)

    result = await ExecutionRouter(callbacks).dispatch(state)

    assert result is state
    spies["direct_answer"].assert_awaited_once_with(state)
    planner.assert_not_awaited()
