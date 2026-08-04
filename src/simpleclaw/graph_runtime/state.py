"""V4 reducer가 단독으로 갱신하는 typed runtime snapshot."""

from __future__ import annotations

from typing import Annotated, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from .contracts import RequestEnvelopeV1
from .events import ActionReceiptV1, DeliveryReceiptV1, GraphEventV1, RouteTransitionV1
from .status import DeliveryStatus, EffectStatus, LifecycleStatus, PlanStatus, TerminalOutcome


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
    graph_steps: int = Field(default=0, ge=0)
    asset_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)


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


def new_runtime_snapshot(request_id: str, *, initial_route: str | None = None) -> RuntimeSnapshotV1:
    route = RouteStateV1(initial_route=initial_route, active_route=initial_route)
    return RuntimeSnapshotV1(request=RequestStateV1(request_id=request_id), route=route)


def _append_only(left: tuple, right: tuple) -> tuple:
    """LangGraph annotation용 연결 함수; 의미적 중복 검증은 reduce_state가 한다."""
    return left + right


class RuntimeGraphState(TypedDict):
    envelope: RequestEnvelopeV1
    events: Annotated[tuple[GraphEventV1, ...], _append_only]
    route_transitions: Annotated[tuple[RouteTransitionV1, ...], _append_only]
    action_receipts: Annotated[tuple[ActionReceiptV1, ...], _append_only]
    delivery_receipts: Annotated[tuple[DeliveryReceiptV1, ...], _append_only]
    snapshot: RuntimeSnapshotV1
