"""Append-only V4 graph event와 외부 동작 receipt 계약."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .status import (
    DeliveryStatus,
    EffectStatus,
    LifecycleStatus,
    PlanStatus,
    TerminalOutcome,
)


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GraphEventKind(str, Enum):
    LIFECYCLE_CHANGED = "lifecycle_changed"
    PLAN_STATUS_CHANGED = "plan_status_changed"
    CANCELLATION_REQUESTED = "cancellation_requested"
    TERMINAL_OUTCOME_PROPOSED = "terminal_outcome_proposed"
    BUDGET_CONSUMED = "budget_consumed"
    CHECKPOINT_ADVANCED = "checkpoint_advanced"
    ARTIFACT_RECORDED = "artifact_recorded"
    DELIVERY_STATUS_CHANGED = "delivery_status_changed"


class BudgetDeltaV1(LedgerModel):
    graph_steps: int = Field(default=0, ge=0)
    asset_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_consumption(self) -> BudgetDeltaV1:
        if not any((self.graph_steps, self.asset_calls, self.llm_calls, self.tokens)):
            raise ValueError("budget consumption must increment at least one axis")
        return self


class GraphEventV1(LedgerModel):
    schema_version: Literal["graph_event.v1"] = Field(
        default="graph_event.v1", alias="schema"
    )
    event_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    occurred_at: datetime
    kind: GraphEventKind
    lifecycle: LifecycleStatus | None = None
    plan_status: PlanStatus | None = None
    terminal_outcome: TerminalOutcome | None = None
    delivery_status: DeliveryStatus | None = None
    budget_delta: BudgetDeltaV1 | None = None
    checkpoint_version: int | None = Field(default=None, ge=0)
    artifact_id: str | None = None
    artifact_hash: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_kind_payload(self) -> GraphEventV1:
        required = {
            GraphEventKind.LIFECYCLE_CHANGED: self.lifecycle,
            GraphEventKind.PLAN_STATUS_CHANGED: self.plan_status,
            GraphEventKind.TERMINAL_OUTCOME_PROPOSED: self.terminal_outcome,
            GraphEventKind.BUDGET_CONSUMED: self.budget_delta,
            GraphEventKind.CHECKPOINT_ADVANCED: self.checkpoint_version,
            GraphEventKind.ARTIFACT_RECORDED: self.artifact_id,
            GraphEventKind.DELIVERY_STATUS_CHANGED: self.delivery_status,
        }
        if self.kind in required and required[self.kind] is None:
            raise ValueError(f"{self.kind.value} requires its matching payload")
        return self


class RouteTransitionV1(LedgerModel):
    schema_version: Literal["route_transition.v1"] = Field(
        default="route_transition.v1", alias="schema"
    )
    transition_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    occurred_at: datetime
    from_route: str = Field(min_length=1)
    to_route: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    remaining_graph_steps: int = Field(ge=0)
    remaining_asset_calls: int = Field(ge=0)
    remaining_llm_calls: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def reject_self_transition(self) -> RouteTransitionV1:
        if self.from_route == self.to_route:
            raise ValueError("route transition must change active_route")
        return self


class ActionReceiptV1(LedgerModel):
    schema_version: Literal["action_receipt.v1"] = Field(
        default="action_receipt.v1", alias="schema"
    )
    receipt_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    occurred_at: datetime
    effect_status: EffectStatus
    detail: str | None = None


class DeliveryReceiptV1(LedgerModel):
    schema_version: Literal["delivery_receipt.v1"] = Field(
        default="delivery_receipt.v1", alias="schema"
    )
    receipt_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    delivery_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    occurred_at: datetime
    status: DeliveryStatus
    attempt: int = Field(default=1, gt=0)
    detail: str | None = None
