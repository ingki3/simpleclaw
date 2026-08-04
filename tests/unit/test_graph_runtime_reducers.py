from datetime import UTC, datetime

import pytest

from simpleclaw.graph_runtime.events import (
    ActionReceiptV1,
    GraphEventKind,
    GraphEventV1,
    RouteTransitionV1,
)
from simpleclaw.graph_runtime.reducers import ReducerInvariantError, reduce_state
from simpleclaw.graph_runtime.state import new_runtime_snapshot
from simpleclaw.graph_runtime.status import (
    EffectStatus,
    LifecycleStatus,
    TerminalOutcome,
)

NOW = datetime(2026, 8, 5, tzinfo=UTC)


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
    snapshot = new_runtime_snapshot("request-1", initial_route="react")
    transition = RouteTransitionV1(
        transition_id="route-1",
        request_id="request-1",
        sequence=1,
        occurred_at=NOW,
        from_route="react",
        to_route="deep_research",
        reason="bounded escalation",
        remaining_graph_steps=4,
        remaining_asset_calls=3,
        remaining_llm_calls=2,
        remaining_tokens=1000,
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
    receipt = ActionReceiptV1(
        receipt_id="receipt-1",
        request_id="request-1",
        invocation_id="invoke-1",
        idempotency_key="effect-1",
        sequence=1,
        occurred_at=NOW,
        effect_status=EffectStatus.UNKNOWN,
    )
    reduced = reduce_state(snapshot, events=(_cancel(),), action_receipts=(receipt,))
    assert reduced.effect_status is EffectStatus.UNKNOWN
    assert reduced.terminal_outcome is TerminalOutcome.BLOCKED
    assert reduced.lifecycle is LifecycleStatus.TERMINAL
