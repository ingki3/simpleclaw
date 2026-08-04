"""V4 reducer가 단독으로 갱신하는 typed runtime snapshot."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Annotated, TypedDict, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ExecutionBudgetV1, RequestEnvelopeV1
from .events import ActionReceiptV1, DeliveryReceiptV1, GraphEventV1, RouteTransitionV1
from .status import (
    DeliveryStatus,
    EffectStatus,
    LifecycleStatus,
    PlanStatus,
    TerminalOutcome,
)


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RequestStateV1(SnapshotModel):
    request_id: str = Field(min_length=1)
    cancellation_requested: bool = False


class RouteStateV1(SnapshotModel):
    initial_route: str | None = None
    active_route: str | None = None


class CheckpointStateV1(SnapshotModel):
    version: int = Field(default=0, ge=0)


class BudgetStateV1(SnapshotModel):
    max_graph_steps: int = Field(default=0, ge=0)
    max_asset_calls: int = Field(default=0, ge=0)
    max_llm_calls: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=0, ge=0)
    graph_steps: int = Field(default=0, ge=0)
    asset_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    remaining_graph_steps: int = Field(default=0, ge=0)
    remaining_asset_calls: int = Field(default=0, ge=0)
    remaining_llm_calls: int = Field(default=0, ge=0)
    remaining_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_conserved_totals(self) -> BudgetStateV1:
        axes = (
            (self.max_graph_steps, self.graph_steps, self.remaining_graph_steps),
            (self.max_asset_calls, self.asset_calls, self.remaining_asset_calls),
            (self.max_llm_calls, self.llm_calls, self.remaining_llm_calls),
            (self.max_tokens, self.tokens, self.remaining_tokens),
        )
        if any(limit != consumed + remaining for limit, consumed, remaining in axes):
            raise ValueError(
                "budget max must equal consumed plus remaining on every axis"
            )
        return self


class ArtifactStateV1(SnapshotModel):
    artifact_id: str | None = None
    artifact_hash: str | None = None


class DeliveryStateV1(SnapshotModel):
    status: DeliveryStatus = DeliveryStatus.NOT_READY
    delivery_id: str | None = None
    attempts: int = Field(default=0, ge=0)


class RuntimeSnapshotV1(SnapshotModel):
    request: RequestStateV1
    route: RouteStateV1 = RouteStateV1()
    checkpoint: CheckpointStateV1 = CheckpointStateV1()
    budget: BudgetStateV1 = BudgetStateV1()
    artifact: ArtifactStateV1 = ArtifactStateV1()
    delivery: DeliveryStateV1 = DeliveryStateV1()
    lifecycle: LifecycleStatus = LifecycleStatus.NEW
    plan_status: PlanStatus = PlanStatus.ABSENT
    effect_status: EffectStatus = EffectStatus.NONE
    terminal_outcome: TerminalOutcome | None = None
    processed_ledger_ids: frozenset[str] = frozenset()
    processed_ledger_fingerprints: tuple[tuple[str, str], ...] = ()


def new_runtime_snapshot(
    request_id: str,
    *,
    initial_route: str | None = None,
    execution_budget: ExecutionBudgetV1 | None = None,
) -> RuntimeSnapshotV1:
    route = RouteStateV1(initial_route=initial_route, active_route=initial_route)
    budget = BudgetStateV1()
    if execution_budget is not None:
        budget = BudgetStateV1(
            max_graph_steps=execution_budget.max_graph_steps,
            max_asset_calls=execution_budget.max_asset_calls,
            max_llm_calls=execution_budget.max_llm_calls,
            max_tokens=execution_budget.max_tokens,
            remaining_graph_steps=execution_budget.max_graph_steps,
            remaining_asset_calls=execution_budget.max_asset_calls,
            remaining_llm_calls=execution_budget.max_llm_calls,
            remaining_tokens=execution_budget.max_tokens,
        )
    return RuntimeSnapshotV1(
        request=RequestStateV1(request_id=request_id), route=route, budget=budget
    )


_LedgerT = TypeVar("_LedgerT")


def _append_unique(
    current: Sequence[_LedgerT],
    incoming: Iterable[_LedgerT],
    *,
    identity: Callable[[_LedgerT], str],
) -> tuple[_LedgerT, ...]:
    """LangGraph channel 경계에서 canonical identity 충돌을 거부한다."""
    merged = list(current)
    by_identity = {identity(item): item for item in current}
    for item in incoming:
        key = identity(item)
        existing = by_identity.get(key)
        if existing is None:
            by_identity[key] = item
            merged.append(item)
        elif existing != item:
            raise ValueError(f"conflicting append-only ledger identity: {key}")
    return tuple(merged)


def append_unique_event(current, incoming):
    return _append_unique(current, incoming, identity=lambda item: item.event_id)


def append_unique_route_transition(current, incoming):
    return _append_unique(current, incoming, identity=lambda item: item.transition_id)


def append_unique_receipt(current, incoming):
    return _append_unique(current, incoming, identity=lambda item: item.idempotency_key)


def append_unique_delivery_receipt(current, incoming):
    return _append_unique(current, incoming, identity=lambda item: item.receipt_id)


class RuntimeGraphState(TypedDict):
    envelope: RequestEnvelopeV1
    events: Annotated[tuple[GraphEventV1, ...], append_unique_event]
    route_transitions: Annotated[
        tuple[RouteTransitionV1, ...], append_unique_route_transition
    ]
    action_receipts: Annotated[tuple[ActionReceiptV1, ...], append_unique_receipt]
    delivery_receipts: Annotated[
        tuple[DeliveryReceiptV1, ...], append_unique_delivery_receipt
    ]
    snapshot: RuntimeSnapshotV1
