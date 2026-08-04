"""Append-only ledger를 typed snapshot으로 투영하는 유일한 writer."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from .events import (
    ActionReceiptV1,
    DeliveryReceiptV1,
    GraphEventKind,
    GraphEventV1,
    RouteTransitionV1,
)
from .state import (
    ArtifactStateV1,
    BudgetStateV1,
    CheckpointStateV1,
    DeliveryStateV1,
    RouteStateV1,
    RuntimeSnapshotV1,
    append_unique_delivery_receipt,
    append_unique_event,
    append_unique_receipt,
    append_unique_route_transition,
)
from .status import (
    EffectStatus,
    LifecycleStatus,
    TerminalOutcome,
    require_legal_transition,
    select_terminal_outcome,
)

__all__ = [
    "ReducerInvariantError",
    "append_unique_delivery_receipt",
    "append_unique_event",
    "append_unique_receipt",
    "append_unique_route_transition",
    "reduce_state",
]


class ReducerInvariantError(ValueError):
    """Ledger 충돌 또는 snapshot 불변식 위반을 fail-closed로 알린다."""


def _ensure_request(snapshot: RuntimeSnapshotV1, request_id: str) -> None:
    if request_id != snapshot.request.request_id:
        raise ReducerInvariantError(
            f"ledger request_id {request_id!r} does not match snapshot request"
        )


def reduce_state(
    snapshot: RuntimeSnapshotV1,
    *,
    events: Sequence[GraphEventV1] = (),
    route_transitions: Sequence[RouteTransitionV1] = (),
    action_receipts: Sequence[ActionReceiptV1] = (),
    delivery_receipts: Sequence[DeliveryReceiptV1] = (),
) -> RuntimeSnapshotV1:
    """새 ledger 항목만 순서대로 적용하고 immutable snapshot을 반환한다."""
    ledgers: list[tuple[int, str, BaseModel]] = []
    ledgers.extend((item.sequence, f"event:{item.event_id}", item) for item in events)
    ledgers.extend(
        (item.sequence, f"route_transition:{item.transition_id}", item)
        for item in route_transitions
    )
    ledgers.extend(
        (item.sequence, f"action_receipt:{item.idempotency_key}", item)
        for item in action_receipts
    )
    ledgers.extend(
        (item.sequence, f"delivery_receipt:{item.receipt_id}", item)
        for item in delivery_receipts
    )

    seen_batch: dict[str, BaseModel] = {}
    for _, ledger_id, item in ledgers:
        existing = seen_batch.get(ledger_id)
        if existing is not None and existing != item:
            raise ReducerInvariantError(f"conflicting ledger id: {ledger_id}")
        seen_batch[ledger_id] = item

    current = snapshot
    prior_fingerprints = dict(snapshot.processed_ledger_fingerprints)
    for _, ledger_id, item in sorted(ledgers, key=lambda value: (value[0], value[1])):
        fingerprint = item.model_dump_json()
        if ledger_id in current.processed_ledger_ids:
            if prior_fingerprints.get(ledger_id) != fingerprint:
                raise ReducerInvariantError(
                    f"conflicting previously processed ledger id: {ledger_id}"
                )
            continue
        request_id = item.request_id
        _ensure_request(current, request_id)
        current = _apply_item(current, item)
        prior_fingerprints[ledger_id] = fingerprint
        current = current.model_copy(
            update={
                "processed_ledger_ids": current.processed_ledger_ids | {ledger_id},
                "processed_ledger_fingerprints": tuple(
                    sorted(prior_fingerprints.items())
                ),
            }
        )
    return _apply_terminal_lattice(current)


def _apply_item(snapshot: RuntimeSnapshotV1, item: BaseModel) -> RuntimeSnapshotV1:
    if isinstance(item, GraphEventV1):
        return _apply_event(snapshot, item)
    if isinstance(item, RouteTransitionV1):
        route = snapshot.route
        if route.initial_route is None:
            raise ReducerInvariantError(
                "initial_route must be set before route transition"
            )
        if route.active_route != item.from_route:
            raise ReducerInvariantError("route transition from_route is stale")
        remaining = (
            item.remaining_graph_steps,
            item.remaining_asset_calls,
            item.remaining_llm_calls,
            item.remaining_tokens,
        )
        snapshot_remaining = (
            snapshot.budget.remaining_graph_steps,
            snapshot.budget.remaining_asset_calls,
            snapshot.budget.remaining_llm_calls,
            snapshot.budget.remaining_tokens,
        )
        if remaining != snapshot_remaining:
            raise ReducerInvariantError(
                "route transition remaining budget does not match snapshot"
            )
        return snapshot.model_copy(
            update={
                "route": RouteStateV1(
                    initial_route=route.initial_route, active_route=item.to_route
                ),
                "budget": snapshot.budget.model_copy(),
            }
        )
    if isinstance(item, ActionReceiptV1):
        terminal_receipt_statuses = {
            EffectStatus.VERIFIED,
            EffectStatus.DENIED,
            EffectStatus.UNKNOWN,
            EffectStatus.PARTIAL,
            EffectStatus.FAILED,
        }
        if item.effect_status not in terminal_receipt_statuses:
            raise ReducerInvariantError(
                "action receipt must contain a terminal effect status"
            )
        # Durable receipt는 checkpoint보다 먼저 저장될 수 있다. fresh snapshot에서
        # replay할 때도 terminal 결과를 복원하되, 기록된 lifecycle은 엄격히 검증한다.
        if snapshot.effect_status is not EffectStatus.NONE:
            require_legal_transition(snapshot.effect_status, item.effect_status)
        return snapshot.model_copy(update={"effect_status": item.effect_status})
    if isinstance(item, DeliveryReceiptV1):
        require_legal_transition(snapshot.delivery.status, item.status)
        if snapshot.delivery.delivery_id not in {None, item.delivery_id}:
            raise ReducerInvariantError("delivery_id cannot change")
        return snapshot.model_copy(
            update={
                "delivery": DeliveryStateV1(
                    status=item.status,
                    delivery_id=item.delivery_id,
                    attempts=max(snapshot.delivery.attempts, item.attempt),
                )
            }
        )
    raise ReducerInvariantError(f"unsupported ledger type: {type(item).__name__}")


def _apply_event(snapshot: RuntimeSnapshotV1, event: GraphEventV1) -> RuntimeSnapshotV1:
    if event.kind is GraphEventKind.LIFECYCLE_CHANGED:
        require_legal_transition(snapshot.lifecycle, event.lifecycle)
        return snapshot.model_copy(update={"lifecycle": event.lifecycle})
    if event.kind is GraphEventKind.PLAN_STATUS_CHANGED:
        require_legal_transition(snapshot.plan_status, event.plan_status)
        return snapshot.model_copy(update={"plan_status": event.plan_status})
    if event.kind is GraphEventKind.CANCELLATION_REQUESTED:
        return snapshot.model_copy(
            update={
                "request": snapshot.request.model_copy(
                    update={"cancellation_requested": True}
                )
            }
        )
    if event.kind is GraphEventKind.TERMINAL_OUTCOME_PROPOSED:
        selected = select_terminal_outcome(
            [
                candidate
                for candidate in (snapshot.terminal_outcome, event.terminal_outcome)
                if candidate
            ],
            effect_status=snapshot.effect_status,
        )
        return snapshot.model_copy(update={"terminal_outcome": selected})
    if event.kind is GraphEventKind.BUDGET_CONSUMED:
        delta = event.budget_delta
        budget = snapshot.budget
        remaining = (
            budget.remaining_graph_steps - delta.graph_steps,
            budget.remaining_asset_calls - delta.asset_calls,
            budget.remaining_llm_calls - delta.llm_calls,
            budget.remaining_tokens - delta.tokens,
        )
        if any(value < 0 for value in remaining):
            raise ReducerInvariantError("budget consumption exceeds remaining budget")
        return snapshot.model_copy(
            update={
                "budget": BudgetStateV1(
                    max_graph_steps=budget.max_graph_steps,
                    max_asset_calls=budget.max_asset_calls,
                    max_llm_calls=budget.max_llm_calls,
                    max_tokens=budget.max_tokens,
                    graph_steps=budget.graph_steps + delta.graph_steps,
                    asset_calls=budget.asset_calls + delta.asset_calls,
                    llm_calls=budget.llm_calls + delta.llm_calls,
                    tokens=budget.tokens + delta.tokens,
                    remaining_graph_steps=remaining[0],
                    remaining_asset_calls=remaining[1],
                    remaining_llm_calls=remaining[2],
                    remaining_tokens=remaining[3],
                )
            }
        )
    if event.kind is GraphEventKind.CHECKPOINT_ADVANCED:
        if event.checkpoint_version <= snapshot.checkpoint.version:
            raise ReducerInvariantError("checkpoint_version must increase")
        return snapshot.model_copy(
            update={"checkpoint": CheckpointStateV1(version=event.checkpoint_version)}
        )
    if event.kind is GraphEventKind.ARTIFACT_RECORDED:
        if snapshot.artifact.artifact_id not in {None, event.artifact_id}:
            raise ReducerInvariantError("artifact is write-once")
        artifact = ArtifactStateV1(
            artifact_id=event.artifact_id, artifact_hash=event.artifact_hash
        )
        if snapshot.artifact.artifact_id and snapshot.artifact != artifact:
            raise ReducerInvariantError("artifact bytes cannot change")
        return snapshot.model_copy(update={"artifact": artifact})
    if event.kind is GraphEventKind.DELIVERY_STATUS_CHANGED:
        require_legal_transition(snapshot.delivery.status, event.delivery_status)
        return snapshot.model_copy(
            update={
                "delivery": snapshot.delivery.model_copy(
                    update={"status": event.delivery_status}
                )
            }
        )
    if event.kind is GraphEventKind.EFFECT_STATUS_CHANGED:
        if event.effect_status in {
            EffectStatus.VERIFIED,
            EffectStatus.UNKNOWN,
            EffectStatus.PARTIAL,
            EffectStatus.FAILED,
        }:
            raise ReducerInvariantError(
                "terminal effect status requires an action receipt"
            )
        require_legal_transition(snapshot.effect_status, event.effect_status)
        return snapshot.model_copy(update={"effect_status": event.effect_status})
    raise ReducerInvariantError(f"unknown graph event kind: {event.kind}")


def _apply_terminal_lattice(snapshot: RuntimeSnapshotV1) -> RuntimeSnapshotV1:
    candidates = [snapshot.terminal_outcome] if snapshot.terminal_outcome else []
    if snapshot.request.cancellation_requested:
        candidates.append(TerminalOutcome.CANCELLED)
    selected = select_terminal_outcome(candidates, effect_status=snapshot.effect_status)
    if selected is None:
        return snapshot
    lifecycle = snapshot.lifecycle
    if lifecycle is not LifecycleStatus.TERMINAL:
        if lifecycle is LifecycleStatus.NEW:
            lifecycle = LifecycleStatus.ACTIVE
        require_legal_transition(lifecycle, LifecycleStatus.TERMINAL)
        lifecycle = LifecycleStatus.TERMINAL
    return snapshot.model_copy(
        update={"terminal_outcome": selected, "lifecycle": lifecycle}
    )
