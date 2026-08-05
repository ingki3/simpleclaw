from datetime import UTC, datetime, timedelta
from typing import get_args, get_type_hints

import pytest

from simpleclaw.graph_runtime.contracts import ExecutionBudgetV1
from simpleclaw.graph_runtime.events import (
    ActionReceiptV1,
    BudgetDeltaV1,
    GraphEventKind,
    GraphEventV1,
    RouteTransitionV1,
)
from simpleclaw.graph_runtime.reducers import ReducerInvariantError, reduce_state
from simpleclaw.graph_runtime.state import (
    RuntimeGraphState,
    append_unique_receipt,
    new_runtime_snapshot,
)
from simpleclaw.graph_runtime.status import (
    EffectStatus,
    LifecycleStatus,
    TerminalOutcome,
)

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _budget() -> ExecutionBudgetV1:
    return ExecutionBudgetV1(
        max_graph_steps=10,
        max_asset_calls=5,
        max_llm_calls=3,
        max_tokens=2000,
        deadline_at=NOW + timedelta(minutes=5),
        max_parallel_invocations=2,
    )


def _effect_event(
    event_id: str, sequence: int, effect_status: EffectStatus
) -> GraphEventV1:
    return GraphEventV1(
        event_id=event_id,
        request_id="request-1",
        sequence=sequence,
        occurred_at=NOW,
        kind=GraphEventKind.EFFECT_STATUS_CHANGED,
        effect_status=effect_status,
    )


def _receipt(
    *,
    receipt_id: str = "receipt-1",
    idempotency_key: str = "effect-1",
    sequence: int = 5,
    effect_status: EffectStatus = EffectStatus.VERIFIED,
) -> ActionReceiptV1:
    return ActionReceiptV1(
        receipt_id=receipt_id,
        request_id="request-1",
        invocation_id="invoke-1",
        idempotency_key=idempotency_key,
        sequence=sequence,
        occurred_at=NOW,
        effect_status=effect_status,
    )


def _cancel(event_id: str = "cancel-1") -> GraphEventV1:
    return GraphEventV1(
        event_id=event_id,
        request_id="request-1",
        sequence=2,
        occurred_at=NOW,
        kind=GraphEventKind.CANCELLATION_REQUESTED,
    )


def test_duplicate_event_is_idempotent_and_conflict_fails_closed() -> None:
    snapshot = new_runtime_snapshot("request-1", initial_route="react")
    event = GraphEventV1(
        event_id="active-1",
        request_id="request-1",
        sequence=1,
        occurred_at=NOW,
        kind=GraphEventKind.LIFECYCLE_CHANGED,
        lifecycle=LifecycleStatus.ACTIVE,
    )
    reduced = reduce_state(snapshot, events=(event, event))
    assert reduced.lifecycle is LifecycleStatus.ACTIVE
    assert reduce_state(reduced, events=(event,)) == reduced

    conflicting = event.model_copy(update={"lifecycle": LifecycleStatus.INTERRUPTED})
    with pytest.raises(ReducerInvariantError, match="conflicting ledger id"):
        reduce_state(snapshot, events=(event, conflicting))
    with pytest.raises(ReducerInvariantError, match="previously processed"):
        reduce_state(reduced, events=(conflicting,))


def test_initial_route_is_immutable_and_active_route_is_reducer_owned() -> None:
    snapshot = new_runtime_snapshot(
        "request-1", initial_route="react", execution_budget=_budget()
    )
    transition = RouteTransitionV1(
        transition_id="route-1",
        request_id="request-1",
        sequence=1,
        occurred_at=NOW,
        from_route="react",
        to_route="deep_research",
        reason="bounded escalation",
        remaining_graph_steps=10,
        remaining_asset_calls=5,
        remaining_llm_calls=3,
        remaining_tokens=2000,
    )
    reduced = reduce_state(snapshot, route_transitions=(transition,))
    assert reduced.route.initial_route == "react"
    assert reduced.route.active_route == "deep_research"

    stale = transition.model_copy(
        update={"transition_id": "route-2", "sequence": 2, "from_route": "react"}
    )
    with pytest.raises(ReducerInvariantError, match="stale"):
        reduce_state(reduced, route_transitions=(stale,))


def test_cancel_plus_unknown_effect_converges_to_blocked() -> None:
    snapshot = new_runtime_snapshot("request-1", initial_route="react")
    receipt = _receipt(sequence=1, effect_status=EffectStatus.UNKNOWN)
    reduced = reduce_state(snapshot, events=(_cancel(),), action_receipts=(receipt,))
    assert reduced.effect_status is EffectStatus.UNKNOWN
    assert reduced.terminal_outcome is TerminalOutcome.BLOCKED
    assert reduced.lifecycle is LifecycleStatus.TERMINAL


@pytest.mark.parametrize("terminal", [EffectStatus.VERIFIED, EffectStatus.FAILED])
def test_terminal_action_receipt_replays_into_fresh_snapshot(
    terminal: EffectStatus,
) -> None:
    snapshot = new_runtime_snapshot("request-1", initial_route="react")
    reduced = reduce_state(
        snapshot, action_receipts=(_receipt(effect_status=terminal),)
    )
    assert reduced.effect_status is terminal


def test_confirmation_dispatch_terminal_effect_lifecycle() -> None:
    snapshot = new_runtime_snapshot("request-1", initial_route="react")
    events = (
        _effect_event("effect-1", 1, EffectStatus.NOT_AUTHORIZED),
        _effect_event("effect-2", 2, EffectStatus.CONFIRMATION_REQUIRED),
        _effect_event("effect-3", 3, EffectStatus.AUTHORIZED),
        _effect_event("effect-4", 4, EffectStatus.DISPATCHING),
    )
    reduced = reduce_state(snapshot, events=events, action_receipts=(_receipt(),))
    assert reduced.effect_status is EffectStatus.VERIFIED

    with pytest.raises(ReducerInvariantError, match="terminal effect status requires"):
        reduce_state(
            snapshot,
            events=(_effect_event("effect-bad", 1, EffectStatus.FAILED),),
        )


def test_action_receipt_idempotency_key_is_authoritative_across_calls() -> None:
    annotation = get_type_hints(RuntimeGraphState, include_extras=True)[
        "action_receipts"
    ]
    assert get_args(annotation)[1] is append_unique_receipt

    snapshot = new_runtime_snapshot("request-1", initial_route="react")
    receipt = _receipt(effect_status=EffectStatus.UNKNOWN)
    reduced = reduce_state(snapshot, action_receipts=(receipt, receipt))
    assert reduced.effect_status is EffectStatus.UNKNOWN
    assert reduce_state(reduced, action_receipts=(receipt,)) == reduced

    conflicting = receipt.model_copy(
        update={"receipt_id": "receipt-2", "effect_status": EffectStatus.PARTIAL}
    )
    with pytest.raises(ReducerInvariantError, match="conflicting ledger id"):
        reduce_state(snapshot, action_receipts=(receipt, conflicting))
    with pytest.raises(ReducerInvariantError, match="previously processed"):
        reduce_state(reduced, action_receipts=(conflicting,))
    with pytest.raises(ValueError, match="conflicting append-only ledger identity"):
        append_unique_receipt((receipt,), (conflicting,))


@pytest.mark.parametrize(
    "remaining",
    [
        (8, 5, 3, 2000),
        (10, 5, 3, 2000),
        (7, 4, 3, 2000),
        (7, 5, 2, 2000),
        (7, 5, 3, 1999),
    ],
)
def test_route_transition_rejects_budget_increase_reset_or_mismatch(
    remaining: tuple[int, int, int, int],
) -> None:
    snapshot = new_runtime_snapshot(
        "request-1", initial_route="react", execution_budget=_budget()
    )
    consumed = GraphEventV1(
        event_id="budget-1",
        request_id="request-1",
        sequence=1,
        occurred_at=NOW,
        kind=GraphEventKind.BUDGET_CONSUMED,
        budget_delta=BudgetDeltaV1(graph_steps=3),
    )
    transition = RouteTransitionV1(
        transition_id="route-1",
        request_id="request-1",
        sequence=2,
        occurred_at=NOW,
        from_route="react",
        to_route="deep_research",
        reason="bounded escalation",
        remaining_graph_steps=remaining[0],
        remaining_asset_calls=remaining[1],
        remaining_llm_calls=remaining[2],
        remaining_tokens=remaining[3],
    )
    with pytest.raises(ReducerInvariantError, match="remaining budget"):
        reduce_state(snapshot, events=(consumed,), route_transitions=(transition,))


def test_route_transition_inherits_exact_remaining_budget() -> None:
    snapshot = new_runtime_snapshot(
        "request-1", initial_route="react", execution_budget=_budget()
    )
    consumed = GraphEventV1(
        event_id="budget-1",
        request_id="request-1",
        sequence=1,
        occurred_at=NOW,
        kind=GraphEventKind.BUDGET_CONSUMED,
        budget_delta=BudgetDeltaV1(
            graph_steps=3, asset_calls=1, llm_calls=1, tokens=500
        ),
    )
    transition = RouteTransitionV1(
        transition_id="route-1",
        request_id="request-1",
        sequence=2,
        occurred_at=NOW,
        from_route="react",
        to_route="deep_research",
        reason="bounded escalation",
        remaining_graph_steps=7,
        remaining_asset_calls=4,
        remaining_llm_calls=2,
        remaining_tokens=1500,
    )
    reduced = reduce_state(
        snapshot, events=(consumed,), route_transitions=(transition,)
    )
    assert reduced.route.active_route == "deep_research"
    assert reduced.budget.graph_steps == 3
    assert reduced.budget.remaining_graph_steps == 7
