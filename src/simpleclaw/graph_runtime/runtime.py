"""Delivery journal과 ConversationStore persistence recovery facade."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import sqlite3
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from .adapters.delivery import (
    AdapterDeliveryResult,
    DeliveryAdapter,
    NullDeliveryAdapter,
)
from .checkpoint import resolve_checkpoint_path
from .composition import FinalCompositionRuntime
from .contracts import (
    AssetBindingRefV1,
    AssetDefinitionSnapshotV1,
    AssetInvocationV1,
    AssetRefV1,
    ContractRefV1,
    DeliveryIntentV1,
    FinalArtifactV1,
    NormalizedAssetResultV1,
)
from .contracts_registry import RegistryAssetEntryV1
from .events import DeliveryReceiptV1
from .idempotency import (
    IdempotencyInvariantError,
    UniquePayloadLedger,
    persistence_id,
    validate_canonical_artifact_identity,
)
from .status import (
    AssetResultStatus,
    DeliveryStatus,
    EffectStatus,
    InvocationStatus,
    TerminalOutcome,
)

ShadowStopCondition = Literal[
    "completed",
    "budget_exhausted",
    "deadline",
    "cancelled",
    "failed",
    "blocked",
]

LangGraphV4ExecutionMode = Literal[
    "shadow",
    "read_only_canary",
    "primary",
]


@dataclass(frozen=True, slots=True)
class ShadowBudgetUsageV1:
    """Shadow 한 run의 유한 budget과 실제 사용량/중단 이유다."""

    max_graph_steps: int
    max_asset_calls: int
    max_llm_calls: int
    max_tokens: int
    max_seconds: float
    max_parallel_invocations: int
    graph_steps: int
    asset_calls: int
    llm_calls: int
    tokens: int
    elapsed_seconds: float
    parallel_peak: int
    stop_condition: ShadowStopCondition

    def __post_init__(self) -> None:
        limits = (
            self.max_graph_steps,
            self.max_asset_calls,
            self.max_llm_calls,
            self.max_tokens,
            self.max_seconds,
            self.max_parallel_invocations,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
            for value in limits
        ):
            raise ValueError("all shadow budget limits must be finite and positive")
        usage = (
            self.graph_steps,
            self.asset_calls,
            self.llm_calls,
            self.tokens,
            self.elapsed_seconds,
            self.parallel_peak,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value < 0
            for value in usage
        ):
            raise ValueError("shadow budget usage must be finite and non-negative")
        if not self.stop_condition:
            raise ValueError("shadow stop condition is required")

    @property
    def exhausted(self) -> bool:
        return self.stop_condition in {"budget_exhausted", "deadline"} or any(
            (
                self.graph_steps >= self.max_graph_steps,
                self.asset_calls >= self.max_asset_calls,
                self.llm_calls >= self.max_llm_calls,
                self.tokens >= self.max_tokens,
                self.elapsed_seconds >= self.max_seconds,
                self.parallel_peak >= self.max_parallel_invocations,
            )
        )

    def as_dict(self) -> dict[str, int | float | str | bool]:
        return {
            "max_graph_steps": self.max_graph_steps,
            "max_asset_calls": self.max_asset_calls,
            "max_llm_calls": self.max_llm_calls,
            "max_tokens": self.max_tokens,
            "max_seconds": self.max_seconds,
            "max_parallel_invocations": self.max_parallel_invocations,
            "graph_steps": self.graph_steps,
            "asset_calls": self.asset_calls,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "parallel_peak": self.parallel_peak,
            "stop_condition": self.stop_condition,
            "exhausted": self.exhausted,
        }


class ShadowNoSendConfigurationError(ValueError):
    """Shadow facade가 live adapter를 보유할 수 있는 설정을 거부한다."""


class LangGraphV4RolloutFacade:
    """격리 V4 실행을 typed rollout mode와 no-send graph 경계에 묶는다."""

    def __init__(
        self,
        *,
        architecture: str,
        mode: str,
        shadow_no_send: bool,
        budget: ShadowBudgetUsageV1,
        checkpoint_path: str | Path,
        daemon_db_path: str | Path | None = None,
        conversations_db_path: str | Path | None = None,
    ) -> None:
        if architecture != "langgraph_v4":
            raise ShadowNoSendConfigurationError(
                "rollout facade requires langgraph_v4 architecture"
            )
        if mode == "canary":
            mode = "read_only_canary"
        if mode not in {"shadow", "read_only_canary", "primary"}:
            raise ShadowNoSendConfigurationError(
                "rollout facade requires shadow, read_only_canary, or primary mode"
            )
        if not shadow_no_send:
            raise ShadowNoSendConfigurationError(
                "connected rollout requires shadow_no_send graph delivery"
            )
        self.architecture = architecture
        self.mode = cast(LangGraphV4ExecutionMode, mode)
        self.shadow_no_send = shadow_no_send
        self.budget = budget
        self.checkpoint_path = resolve_checkpoint_path(
            checkpoint_path,
            daemon_db_path=daemon_db_path,
            conversations_db_path=conversations_db_path,
        )

    def shadow_delivery_runtime(self, journal: DeliveryJournal) -> DeliveryRuntime:
        """외부 delivery를 소유하지 않는 NullDeliveryAdapter runtime만 만든다."""
        return DeliveryRuntime(
            journal=journal,
            adapters={
                "telegram": NullDeliveryAdapter(),
                "cron": NullDeliveryAdapter(),
                "internal": NullDeliveryAdapter(),
            },
        )

    def compare(
        self,
        legacy: LegacyRunTelemetryV1,
        shadow: ShadowRunTelemetryV1,
        *,
        side_effect_counts: ShadowSideEffectCountsV1,
    ) -> ShadowComparisonTelemetryV1:
        """고정 no-send 설정 아래 per-run rollback telemetry를 만든다."""
        return compare_shadow_run(
            legacy,
            shadow,
            side_effect_counts=side_effect_counts,
        )


@dataclass(frozen=True, slots=True)
class ShadowSideEffectCountsV1:
    """Shadow에서 반드시 0이어야 하는 live callback 호출 수다."""

    telegram_send: int = 0
    conversation_write: int = 0
    notifier: int = 0

    def __post_init__(self) -> None:
        values = (self.telegram_send, self.conversation_write, self.notifier)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("side-effect counters must be non-negative integers")

    @property
    def total(self) -> int:
        return self.telegram_send + self.conversation_write + self.notifier

    def as_dict(self) -> dict[str, int]:
        return {
            "telegram_send": self.telegram_send,
            "conversation_write": self.conversation_write,
            "notifier": self.notifier,
        }


@dataclass(frozen=True, slots=True)
class TargetDispatchTraceV1:
    """선택된 helper의 단일 실행 불변식을 원문 없이 기록한다."""

    target_asset_ref: AssetRefV1
    invocation_id: str
    attempted: int
    executed: int
    succeeded: int
    duplicate_blocked: int = 0

    def __post_init__(self) -> None:
        if not self.invocation_id:
            raise ValueError("target dispatch invocation_id is required")
        values = (
            self.attempted,
            self.executed,
            self.succeeded,
            self.duplicate_blocked,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in values
        ):
            raise ValueError("target dispatch counters must be non-negative integers")
        if self.executed > self.attempted or self.succeeded > self.executed:
            raise ValueError("target dispatch counters are inconsistent")

    @property
    def exactly_once(self) -> bool:
        return (
            self.attempted == 1
            and self.executed == 1
            and self.succeeded == 1
            and self.duplicate_blocked == 0
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "target_asset_ref": self.target_asset_ref.model_dump(mode="json"),
            "invocation_id": self.invocation_id,
            "attempted": self.attempted,
            "executed": self.executed,
            "succeeded": self.succeeded,
            "duplicate_blocked": self.duplicate_blocked,
            "exactly_once": self.exactly_once,
        }


@dataclass(frozen=True, slots=True)
class LangGraphV4ExecutionReceiptV1:
    """primary/canary 응답 승격 전에 검증하는 typed graph execution receipt다."""

    mode: LangGraphV4ExecutionMode
    request_id: str
    selected_route: str
    final_artifact: FinalArtifactV1 | None
    dispatch_trace: TargetDispatchTraceV1
    budget_usage: ShadowBudgetUsageV1
    side_effect_counts: ShadowSideEffectCountsV1
    terminal_outcome: TerminalOutcome
    rollback_required: bool
    rollback_reasons: tuple[str, ...]
    effect_status: EffectStatus = EffectStatus.NONE
    result_source: Literal["langgraph_v4"] = "langgraph_v4"

    def __post_init__(self) -> None:
        if self.mode not in {"shadow", "read_only_canary", "primary"}:
            raise ValueError("invalid LangGraph V4 execution receipt mode")
        if not self.request_id or not self.selected_route:
            raise ValueError("execution receipt identifiers are required")
        if self.rollback_required != bool(self.rollback_reasons):
            raise ValueError("rollback flag and reasons must agree")
        if not self.rollback_required:
            if not self.dispatch_trace.exactly_once:
                raise ValueError("successful receipt requires exactly-one dispatch")
            if self.side_effect_counts.total:
                raise ValueError("successful receipt requires zero external side effects")
            if self.effect_status not in {EffectStatus.NONE, EffectStatus.VERIFIED}:
                raise ValueError("successful receipt requires a safe effect status")
            if self.terminal_outcome is not TerminalOutcome.COMPLETED:
                raise ValueError("successful receipt requires completed terminal outcome")
            if self.final_artifact is None:
                raise ValueError("successful receipt requires a final artifact")
            if self.final_artifact.request_id != self.request_id:
                raise ValueError("final artifact request identity mismatch")
            if self.final_artifact.outcome is not self.terminal_outcome:
                raise ValueError("final artifact outcome mismatch")
            validate_canonical_artifact_identity(
                request_id=self.request_id,
                content=self.final_artifact.content,
                artifact_id=self.final_artifact.artifact_id,
                content_hash=self.final_artifact.content_hash,
            )

    @property
    def final_content(self) -> str | None:
        if self.rollback_required or self.final_artifact is None:
            return None
        return self.final_artifact.content

    @property
    def provenance(self) -> str:
        target = self.dispatch_trace.target_asset_ref
        return (
            f"langgraph_v4:{target.type}:{target.name}:"
            f"{self.dispatch_trace.invocation_id}"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "request_id": self.request_id,
            "selected_route": self.selected_route,
            "result_source": self.result_source,
            "provenance": self.provenance,
            "typed_final": self.final_artifact is not None,
            "dispatch_trace": self.dispatch_trace.as_dict(),
            "budget_usage": self.budget_usage.as_dict(),
            "side_effect_counts": self.side_effect_counts.as_dict(),
            "terminal_outcome": self.terminal_outcome.value,
            "rollback_required": self.rollback_required,
            "rollback_reason": ",".join(self.rollback_reasons) or None,
        }


@dataclass(frozen=True, slots=True)
class LegacyRunTelemetryV1:
    """비교에 필요한 legacy facade의 원문 없는 최소 관측값이다."""

    selected_route: str
    terminal_outcome: TerminalOutcome
    model_calls: int
    tokens: int = 0

    def __post_init__(self) -> None:
        if not self.selected_route:
            raise ValueError("legacy selected route is required")
        if isinstance(self.model_calls, bool) or self.model_calls < 0:
            raise ValueError("legacy model_calls must be non-negative")
        if isinstance(self.tokens, bool) or self.tokens < 0:
            raise ValueError("legacy tokens must be non-negative")


@dataclass(frozen=True, slots=True)
class ShadowRunTelemetryV1:
    """Contract identity와 rollout 판정만 담는 V4 allowlisted telemetry다."""

    run_id: str
    request_id: str
    checkpoint_thread_id: str
    plan_id: str
    plan_revision: int
    catalog_fingerprint: str
    invocation_id: str
    definition_fingerprint: str
    contract_owner_ref: AssetRefV1
    input_contract_ref: ContractRefV1
    input_schema_hash: str
    payload_hash: str
    binding_ref: AssetBindingRefV1
    output_contract_ref: ContractRefV1
    output_schema_hash: str
    selected_route: str
    invocation_status: InvocationStatus
    asset_result_status: AssetResultStatus
    effect_status: EffectStatus
    terminal_outcome: TerminalOutcome
    delivery_status: DeliveryStatus
    budget_usage: ShadowBudgetUsageV1
    model_call_attribution: Mapping[str, int]
    dispatch_trace: TargetDispatchTraceV1

    def __post_init__(self) -> None:
        identifiers = (
            self.run_id,
            self.request_id,
            self.checkpoint_thread_id,
            self.plan_id,
            self.catalog_fingerprint,
            self.invocation_id,
            self.definition_fingerprint,
            self.payload_hash,
            self.selected_route,
        )
        if any(not value for value in identifiers):
            raise ValueError("shadow telemetry identifiers must be non-empty")
        if isinstance(self.plan_revision, bool) or self.plan_revision <= 0:
            raise ValueError("plan_revision must be positive")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in self.model_call_attribution.values()
        ):
            raise ValueError("model call attribution must use non-negative integers")

    @classmethod
    def from_contract_run(
        cls,
        *,
        run_id: str,
        request_id: str,
        checkpoint_thread_id: str,
        plan_id: str,
        plan_revision: int,
        catalog_fingerprint: str,
        entry: RegistryAssetEntryV1,
        invocation: AssetInvocationV1,
        selected_route: str,
        invocation_status: InvocationStatus,
        result: NormalizedAssetResultV1,
        effect_status: EffectStatus,
        terminal_outcome: TerminalOutcome,
        delivery_status: DeliveryStatus,
        budget_usage: ShadowBudgetUsageV1,
        model_call_attribution: Mapping[str, int],
        dispatch_trace: TargetDispatchTraceV1 | None = None,
    ) -> ShadowRunTelemetryV1:
        """Registry→invocation→result identity가 정확할 때만 telemetry를 만든다."""
        snapshot = entry.snapshot
        binding_ref = snapshot.declared_binding
        canonical_hash = hashlib.sha256(
            invocation.payload_json.encode("utf-8")
        ).hexdigest()
        continuity = (
            invocation.asset_ref == snapshot.asset_ref,
            invocation.definition_fingerprint == snapshot.definition_fingerprint,
            invocation.input_contract == entry.input_descriptor.ref,
            invocation.output_contract == entry.output_descriptor.ref,
            binding_ref is not None,
            invocation.payload_hash == canonical_hash,
            result.invocation_id == invocation.invocation_id,
            result.output_contract == invocation.output_contract,
        )
        if not all(continuity):
            raise ValueError("shadow contract continuity mismatch")
        assert binding_ref is not None
        return cls(
            run_id=run_id,
            request_id=request_id,
            checkpoint_thread_id=checkpoint_thread_id,
            plan_id=plan_id,
            plan_revision=plan_revision,
            catalog_fingerprint=catalog_fingerprint,
            invocation_id=invocation.invocation_id,
            definition_fingerprint=invocation.definition_fingerprint,
            contract_owner_ref=invocation.asset_ref,
            input_contract_ref=invocation.input_contract,
            input_schema_hash=invocation.input_contract.schema_hash,
            payload_hash=invocation.payload_hash,
            binding_ref=binding_ref,
            output_contract_ref=invocation.output_contract,
            output_schema_hash=invocation.output_contract.schema_hash,
            selected_route=selected_route,
            invocation_status=invocation_status,
            asset_result_status=result.status,
            effect_status=effect_status,
            terminal_outcome=terminal_outcome,
            delivery_status=delivery_status,
            budget_usage=budget_usage,
            model_call_attribution=dict(model_call_attribution),
            dispatch_trace=(
                dispatch_trace
                if dispatch_trace is not None
                else TargetDispatchTraceV1(
                    target_asset_ref=invocation.asset_ref,
                    invocation_id=invocation.invocation_id,
                    attempted=1,
                    executed=1,
                    succeeded=(
                        1
                        if invocation_status is InvocationStatus.SUCCEEDED
                        else 0
                    ),
                )
            ),
        )

    def as_dict(self) -> dict[str, object]:
        """원문·payload 없이 명시적 allowlist만 직렬화한다."""
        return {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "checkpoint_thread_id": self.checkpoint_thread_id,
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "catalog_fingerprint": self.catalog_fingerprint,
            "invocation_id": self.invocation_id,
            "definition_fingerprint": self.definition_fingerprint,
            "contract_owner_ref": self.contract_owner_ref.model_dump(mode="json"),
            "input_contract_ref": self.input_contract_ref.model_dump(mode="json"),
            "input_schema_hash": self.input_schema_hash,
            "payload_hash": self.payload_hash,
            "binding_ref": self.binding_ref.model_dump(mode="json"),
            "output_contract_ref": self.output_contract_ref.model_dump(mode="json"),
            "output_schema_hash": self.output_schema_hash,
            "selected_route": self.selected_route,
            "invocation_status": self.invocation_status.value,
            "asset_result_status": self.asset_result_status.value,
            "effect_status": self.effect_status.value,
            "terminal_outcome": self.terminal_outcome.value,
            "delivery_status": self.delivery_status.value,
            "budget_usage": self.budget_usage.as_dict(),
            "model_call_attribution": dict(self.model_call_attribution),
            "dispatch_trace": self.dispatch_trace.as_dict(),
            "stop_condition": self.budget_usage.stop_condition,
        }


@dataclass(frozen=True, slots=True)
class ShadowComparisonTelemetryV1:
    legacy: LegacyRunTelemetryV1
    shadow: ShadowRunTelemetryV1
    side_effect_counts: ShadowSideEffectCountsV1
    route_matches: bool
    outcome_matches: bool
    rollback_required: bool
    rollback_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "legacy": {
                "selected_route": self.legacy.selected_route,
                "terminal_outcome": self.legacy.terminal_outcome.value,
                "model_calls": self.legacy.model_calls,
                "tokens": self.legacy.tokens,
            },
            "shadow": self.shadow.as_dict(),
            "side_effect_counts": self.side_effect_counts.as_dict(),
            "route_matches": self.route_matches,
            "outcome_matches": self.outcome_matches,
            "rollback_required": self.rollback_required,
            "rollback_reason": ",".join(self.rollback_reasons) or None,
        }


@dataclass(frozen=True, slots=True)
class CanaryGateDecisionV1:
    eligible: bool
    rollback_required: bool
    reasons: tuple[str, ...]


def compare_shadow_run(
    legacy: LegacyRunTelemetryV1,
    shadow: ShadowRunTelemetryV1,
    *,
    side_effect_counts: ShadowSideEffectCountsV1,
) -> ShadowComparisonTelemetryV1:
    """Legacy primary와 V4 shadow를 비교하고 per-run rollback signal을 만든다."""
    route_matches = legacy.selected_route == shadow.selected_route
    outcome_matches = legacy.terminal_outcome is shadow.terminal_outcome
    reasons: list[str] = []
    if not route_matches:
        reasons.append("route_mismatch")
    if not outcome_matches:
        reasons.append("terminal_outcome_mismatch")
    if shadow.delivery_status is not DeliveryStatus.SHADOWED:
        reasons.append("no_send_delivery_violation")
    if shadow.invocation_status is not InvocationStatus.SUCCEEDED:
        reasons.append("invocation_not_succeeded")
    if shadow.asset_result_status is not AssetResultStatus.RESOLVED:
        reasons.append("asset_result_not_resolved")
    if shadow.effect_status not in {EffectStatus.NONE, EffectStatus.VERIFIED}:
        reasons.append("effect_not_safe")
    if shadow.terminal_outcome is not TerminalOutcome.COMPLETED:
        reasons.append("shadow_not_completed")
    if shadow.budget_usage.stop_condition != "completed":
        reasons.append("stop_condition_not_completed")
    if shadow.budget_usage.exhausted:
        reasons.append("budget_exhausted")
    if not shadow.dispatch_trace.exactly_once:
        reasons.append("target_dispatch_not_exactly_once")
    if side_effect_counts.total:
        reasons.append("external_side_effect")
    return ShadowComparisonTelemetryV1(
        legacy=legacy,
        shadow=shadow,
        side_effect_counts=side_effect_counts,
        route_matches=route_matches,
        outcome_matches=outcome_matches,
        rollback_required=bool(reasons),
        rollback_reasons=tuple(reasons),
    )


def evaluate_read_only_canary(
    comparison: ShadowComparisonTelemetryV1,
    assets: list[AssetDefinitionSnapshotV1]
    | tuple[AssetDefinitionSnapshotV1, ...],
) -> CanaryGateDecisionV1:
    """Shadow pass와 read-only snapshot만 canary 후보로 허용한다."""
    reasons = list(comparison.rollback_reasons)
    if not assets:
        reasons.append("no_assets")
    if any(not asset.read_only or asset.side_effects for asset in assets):
        reasons.append("asset_not_read_only")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return CanaryGateDecisionV1(
        eligible=not unique_reasons,
        rollback_required=bool(unique_reasons),
        reasons=unique_reasons,
    )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DeliveryJournalEntry:
    intent: DeliveryIntentV1
    content_hash: str
    dispatching_attempt: int | None = None
    dispatch_owner_token: str | None = None
    dispatch_fencing_token: int = 0
    dispatch_lease_expires_at: float | None = None
    receipt: DeliveryReceiptV1 | None = None


@dataclass(frozen=True, slots=True)
class DeliveryDispatchClaim:
    delivery_id: str
    attempt: int
    owner_token: str
    fencing_token: int
    lease_expires_at: float


class DeliveryJournal(Protocol):
    def get(self, delivery_id: str) -> DeliveryJournalEntry | None: ...
    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None: ...
    def claim_dispatch(
        self,
        delivery_id: str,
        *,
        attempt: int,
        owner_token: str,
        lease_expires_at: float,
    ) -> DeliveryDispatchClaim | None: ...
    def renew_dispatch(
        self,
        claim: DeliveryDispatchClaim,
        *,
        now: float,
        lease_expires_at: float,
    ) -> DeliveryDispatchClaim: ...
    def clear_failed_before_send(self, claim: DeliveryDispatchClaim) -> None: ...
    def record_receipt(
        self,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim | None = None,
        now: float,
    ) -> None: ...
    def record_expired_receipt(
        self,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim,
        now: float,
    ) -> None: ...


class InMemoryDeliveryJournal:
    """테스트/주입용 journal. production은 같은 API의 durable 구현을 주입한다."""

    def __init__(self) -> None:
        self._entries: dict[str, DeliveryJournalEntry] = {}
        self._lock = threading.RLock()

    def get(self, delivery_id: str) -> DeliveryJournalEntry | None:
        with self._lock:
            return self._entries.get(delivery_id)

    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None:
        content_hash = _content_hash(content)
        with self._lock:
            entry = self._entries.get(intent.delivery_id)
            if entry is None:
                self._entries[intent.delivery_id] = DeliveryJournalEntry(
                    intent=intent, content_hash=content_hash
                )
                return
            if entry.intent != intent or entry.content_hash != content_hash:
                raise IdempotencyInvariantError(
                    "delivery_id already exists with a different intent or payload"
                )

    def claim_dispatch(
        self,
        delivery_id: str,
        *,
        attempt: int,
        owner_token: str,
        lease_expires_at: float,
    ) -> DeliveryDispatchClaim | None:
        with self._lock:
            entry = self._entries[delivery_id]
            if entry.receipt is not None or entry.dispatch_owner_token is not None:
                return None
            claim = DeliveryDispatchClaim(
                delivery_id=delivery_id,
                attempt=attempt,
                owner_token=owner_token,
                fencing_token=entry.dispatch_fencing_token + 1,
                lease_expires_at=lease_expires_at,
            )
            self._entries[delivery_id] = replace(
                entry,
                dispatching_attempt=attempt,
                dispatch_owner_token=owner_token,
                dispatch_fencing_token=claim.fencing_token,
                dispatch_lease_expires_at=lease_expires_at,
            )
            return claim

    def renew_dispatch(
        self,
        claim: DeliveryDispatchClaim,
        *,
        now: float,
        lease_expires_at: float,
    ) -> DeliveryDispatchClaim:
        with self._lock:
            entry = self._owned_entry(claim)
            if entry.dispatch_lease_expires_at is None or entry.dispatch_lease_expires_at <= now:
                raise IdempotencyInvariantError("cannot renew an expired delivery lease")
            updated = replace(claim, lease_expires_at=lease_expires_at)
            self._entries[claim.delivery_id] = replace(
                entry, dispatch_lease_expires_at=lease_expires_at
            )
            return updated

    def clear_failed_before_send(self, claim: DeliveryDispatchClaim) -> None:
        with self._lock:
            entry = self._owned_entry(claim)
            self._entries[claim.delivery_id] = replace(
                entry,
                dispatching_attempt=None,
                dispatch_owner_token=None,
                dispatch_lease_expires_at=None,
            )

    def record_receipt(
        self,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim | None = None,
        now: float,
    ) -> None:
        with self._lock:
            entry = self._entries[receipt.delivery_id]
            self._validate_receipt_write(entry, receipt, claim=claim, now=now)
            self._entries[receipt.delivery_id] = replace(
                entry,
                dispatching_attempt=None,
                dispatch_owner_token=None,
                dispatch_lease_expires_at=None,
                receipt=receipt,
            )

    def record_expired_receipt(
        self,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim,
        now: float,
    ) -> None:
        with self._lock:
            entry = self._owned_entry(claim)
            if entry.dispatch_lease_expires_at is None or entry.dispatch_lease_expires_at > now:
                raise IdempotencyInvariantError("delivery lease is still active")
            self._entries[receipt.delivery_id] = replace(
                entry,
                dispatching_attempt=None,
                dispatch_owner_token=None,
                dispatch_lease_expires_at=None,
                receipt=receipt,
            )

    def _owned_entry(self, claim: DeliveryDispatchClaim) -> DeliveryJournalEntry:
        entry = self._entries[claim.delivery_id]
        if (
            entry.receipt is not None
            or entry.dispatch_owner_token != claim.owner_token
            or entry.dispatch_fencing_token != claim.fencing_token
            or entry.dispatching_attempt != claim.attempt
        ):
            raise IdempotencyInvariantError("delivery dispatch owner was fenced")
        return entry

    def _validate_receipt_write(
        self,
        entry: DeliveryJournalEntry,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim | None,
        now: float,
    ) -> None:
        if entry.receipt is not None:
            if entry.receipt != receipt:
                raise IdempotencyInvariantError("delivery receipt is write-once")
            return
        if claim is None:
            if entry.dispatch_owner_token is not None:
                raise IdempotencyInvariantError("active delivery owner requires a claim")
            return
        owned = self._owned_entry(claim)
        if owned.dispatch_lease_expires_at is None or owned.dispatch_lease_expires_at <= now:
            raise IdempotencyInvariantError("delivery dispatch owner lease expired")


class SQLiteDeliveryJournal:
    """process crash 뒤에도 sender-started 상태와 receipt를 보존하는 outbox다."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_delivery_journal (
                    delivery_id TEXT PRIMARY KEY,
                    intent_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    dispatching_attempt INTEGER,
                    dispatch_owner_token TEXT,
                    dispatch_fencing_token INTEGER NOT NULL DEFAULT 0,
                    dispatch_lease_expires_at REAL,
                    receipt_json TEXT
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(graph_delivery_journal)"
                ).fetchall()
            }
            migrations = {
                "dispatch_owner_token": "TEXT",
                "dispatch_fencing_token": "INTEGER NOT NULL DEFAULT 0",
                "dispatch_lease_expires_at": "REAL",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE graph_delivery_journal "
                        f"ADD COLUMN {name} {declaration}"
                    )
            conn.execute(
                "UPDATE graph_delivery_journal "
                "SET dispatch_owner_token = 'legacy-unowned', "
                "dispatch_fencing_token = MAX(dispatch_fencing_token, 1), "
                "dispatch_lease_expires_at = 0 "
                "WHERE receipt_json IS NULL AND dispatching_attempt IS NOT NULL "
                "AND dispatch_owner_token IS NULL"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, delivery_id: str) -> DeliveryJournalEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT intent_json, content_hash, dispatching_attempt, "
                "dispatch_owner_token, dispatch_fencing_token, "
                "dispatch_lease_expires_at, receipt_json "
                "FROM graph_delivery_journal WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryJournalEntry(
            intent=DeliveryIntentV1.model_validate_json(row[0]),
            content_hash=str(row[1]),
            dispatching_attempt=(int(row[2]) if row[2] is not None else None),
            dispatch_owner_token=(str(row[3]) if row[3] is not None else None),
            dispatch_fencing_token=int(row[4]),
            dispatch_lease_expires_at=(
                float(row[5]) if row[5] is not None else None
            ),
            receipt=(
                DeliveryReceiptV1.model_validate_json(row[6])
                if row[6] is not None
                else None
            ),
        )

    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None:
        content_hash = _content_hash(content)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO graph_delivery_journal "
                "(delivery_id, intent_json, content_hash) VALUES (?, ?, ?)",
                (intent.delivery_id, intent.model_dump_json(), content_hash),
            )
            row = conn.execute(
                "SELECT intent_json, content_hash FROM graph_delivery_journal "
                "WHERE delivery_id = ?",
                (intent.delivery_id,),
            ).fetchone()
        assert row is not None
        if (
            DeliveryIntentV1.model_validate_json(row[0]) != intent
            or str(row[1]) != content_hash
        ):
            raise IdempotencyInvariantError(
                "delivery_id already exists with a different intent or payload"
            )

    def claim_dispatch(
        self,
        delivery_id: str,
        *,
        attempt: int,
        owner_token: str,
        lease_expires_at: float,
    ) -> DeliveryDispatchClaim | None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = ?, "
                "dispatch_owner_token = ?, "
                "dispatch_fencing_token = dispatch_fencing_token + 1, "
                "dispatch_lease_expires_at = ? "
                "WHERE delivery_id = ? AND receipt_json IS NULL "
                "AND dispatch_owner_token IS NULL",
                (attempt, owner_token, lease_expires_at, delivery_id),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT dispatch_fencing_token FROM graph_delivery_journal "
                "WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        assert row is not None
        return DeliveryDispatchClaim(
            delivery_id=delivery_id,
            attempt=attempt,
            owner_token=owner_token,
            fencing_token=int(row[0]),
            lease_expires_at=lease_expires_at,
        )

    def renew_dispatch(
        self,
        claim: DeliveryDispatchClaim,
        *,
        now: float,
        lease_expires_at: float,
    ) -> DeliveryDispatchClaim:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatch_lease_expires_at = ? "
                "WHERE delivery_id = ? AND receipt_json IS NULL "
                "AND dispatch_owner_token = ? AND dispatch_fencing_token = ? "
                "AND dispatching_attempt = ? "
                "AND dispatch_lease_expires_at > ?",
                (
                    lease_expires_at,
                    claim.delivery_id,
                    claim.owner_token,
                    claim.fencing_token,
                    claim.attempt,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyInvariantError(
                    "cannot renew expired or fenced delivery lease"
                )
        return replace(claim, lease_expires_at=lease_expires_at)

    def clear_failed_before_send(self, claim: DeliveryDispatchClaim) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL, "
                "dispatch_owner_token = NULL, dispatch_lease_expires_at = NULL "
                "WHERE delivery_id = ? AND receipt_json IS NULL "
                "AND dispatch_owner_token = ? AND dispatch_fencing_token = ? "
                "AND dispatching_attempt = ?",
                (
                    claim.delivery_id,
                    claim.owner_token,
                    claim.fencing_token,
                    claim.attempt,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyInvariantError("delivery dispatch owner was fenced")

    def record_receipt(
        self,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim | None = None,
        now: float,
    ) -> None:
        with self._connect() as conn:
            owner_condition = (
                "AND dispatch_owner_token IS NULL"
                if claim is None
                else (
                    "AND dispatch_owner_token = ? AND dispatch_fencing_token = ? "
                    "AND dispatching_attempt = ? AND dispatch_lease_expires_at > ?"
                )
            )
            params: tuple[object, ...] = (
                (receipt.model_dump_json(), receipt.delivery_id)
                if claim is None
                else (
                    receipt.model_dump_json(),
                    receipt.delivery_id,
                    claim.owner_token,
                    claim.fencing_token,
                    claim.attempt,
                    now,
                )
            )
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL, "
                "dispatch_owner_token = NULL, dispatch_lease_expires_at = NULL, "
                "receipt_json = ? WHERE delivery_id = ? AND receipt_json IS NULL "
                + owner_condition,
                params,
            )
            if cursor.rowcount == 1:
                return
            self._raise_receipt_conflict(conn, receipt)

    def record_expired_receipt(
        self,
        receipt: DeliveryReceiptV1,
        *,
        claim: DeliveryDispatchClaim,
        now: float,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL, "
                "dispatch_owner_token = NULL, dispatch_lease_expires_at = NULL, "
                "receipt_json = ? WHERE delivery_id = ? AND receipt_json IS NULL "
                "AND dispatch_owner_token = ? AND dispatch_fencing_token = ? "
                "AND dispatching_attempt = ? AND dispatch_lease_expires_at <= ?",
                (
                    receipt.model_dump_json(),
                    receipt.delivery_id,
                    claim.owner_token,
                    claim.fencing_token,
                    claim.attempt,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                return
            self._raise_receipt_conflict(conn, receipt)

    @staticmethod
    def _raise_receipt_conflict(
        conn: sqlite3.Connection, receipt: DeliveryReceiptV1
    ) -> None:
        row = conn.execute(
            "SELECT receipt_json FROM graph_delivery_journal WHERE delivery_id = ?",
            (receipt.delivery_id,),
        ).fetchone()
        if row is None:
            raise IdempotencyInvariantError("delivery intent is missing")
        if row[0] is not None:
            existing = DeliveryReceiptV1.model_validate_json(row[0])
            if existing == receipt:
                return
            raise IdempotencyInvariantError("delivery receipt is write-once")
        raise IdempotencyInvariantError("delivery dispatch owner was fenced")


class DeliveryRuntime:
    def __init__(
        self,
        *,
        journal: DeliveryJournal,
        adapters: Mapping[str, DeliveryAdapter],
        lease_seconds: float = 30.0,
        poll_interval: float = 0.01,
        clock: Callable[[], float] = time.time,
        owner_token_factory: Callable[[], str] | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("delivery lease_seconds must be positive")
        if poll_interval <= 0:
            raise ValueError("delivery poll_interval must be positive")
        self._journal = journal
        self._adapters = dict(adapters)
        self._lease_seconds = lease_seconds
        self._poll_interval = poll_interval
        self._clock = clock
        self._owner_token_factory = owner_token_factory or (
            lambda: uuid.uuid4().hex
        )

    async def deliver(
        self, intent: DeliveryIntentV1, content: str
    ) -> DeliveryReceiptV1:
        self._journal.record_intent(intent, content)
        entry = self._journal.get(intent.delivery_id)
        assert entry is not None
        if entry.receipt is not None:
            return entry.receipt
        active_claim = self._claim_from_entry(intent.delivery_id, entry)
        if active_claim is not None:
            return await self._wait_for_terminal(intent, active_claim)

        if intent.status is DeliveryStatus.SHADOWED:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.SHADOWED,
                attempt=1,
            )
            return self._record_unclaimed_or_reuse(receipt)

        adapter = self._adapters.get(intent.channel)
        if adapter is None:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.FAILED_BEFORE_SEND,
                attempt=1,
                detail=f"no delivery adapter for channel {intent.channel!r}",
            )
            return self._record_unclaimed_or_reuse(receipt)

        last: DeliveryReceiptV1 | None = None
        owner_token = self._owner_token_factory()
        for attempt in range(1, intent.max_attempts + 1):
            claim = self._journal.claim_dispatch(
                intent.delivery_id,
                attempt=attempt,
                owner_token=owner_token,
                lease_expires_at=self._clock() + self._lease_seconds,
            )
            if claim is None:
                concurrent = self._journal.get(intent.delivery_id)
                assert concurrent is not None
                if concurrent.receipt is not None:
                    return concurrent.receipt
                concurrent_claim = self._claim_from_entry(
                    intent.delivery_id, concurrent
                )
                if concurrent_claim is None:
                    raise IdempotencyInvariantError(
                        "delivery dispatch claim changed without a terminal receipt"
                    )
                return await self._wait_for_terminal(intent, concurrent_claim)
            result = await self._send_with_lease(adapter, intent, content, claim)
            last = self._receipt(
                intent,
                status=result.status,
                attempt=attempt,
                external_message_id=result.external_message_id,
                detail=result.detail,
            )
            if (
                result.status is DeliveryStatus.FAILED_BEFORE_SEND
                and attempt < intent.max_attempts
            ):
                self._journal.clear_failed_before_send(claim)
                continue
            return self._record_owned_or_reuse(last, claim)
        assert last is not None
        return self._record_owned_or_reuse(last, claim)

    async def _send_with_lease(
        self,
        adapter: DeliveryAdapter,
        intent: DeliveryIntentV1,
        content: str,
        claim: DeliveryDispatchClaim,
    ) -> AdapterDeliveryResult:
        heartbeat = asyncio.create_task(self._renew_lease(claim))
        try:
            return await adapter.send(intent, content)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _renew_lease(self, claim: DeliveryDispatchClaim) -> None:
        interval = self._lease_seconds / 3
        while True:
            await asyncio.sleep(interval)
            now = self._clock()
            try:
                claim = self._journal.renew_dispatch(
                    claim,
                    now=now,
                    lease_expires_at=now + self._lease_seconds,
                )
            except IdempotencyInvariantError:
                return

    async def _wait_for_terminal(
        self,
        intent: DeliveryIntentV1,
        claim: DeliveryDispatchClaim,
    ) -> DeliveryReceiptV1:
        while True:
            entry = self._journal.get(intent.delivery_id)
            assert entry is not None
            if entry.receipt is not None:
                return entry.receipt
            current_claim = self._claim_from_entry(intent.delivery_id, entry)
            if current_claim is None:
                await asyncio.sleep(self._poll_interval)
                continue
            claim = current_claim
            now = self._clock()
            if claim.lease_expires_at <= now:
                unknown = self._receipt(
                    intent,
                    status=DeliveryStatus.UNKNOWN,
                    attempt=claim.attempt,
                    detail="delivery owner lease expired without a durable receipt",
                )
                try:
                    self._journal.record_expired_receipt(
                        unknown, claim=claim, now=now
                    )
                    return unknown
                except IdempotencyInvariantError:
                    resolved = self._journal.get(intent.delivery_id)
                    assert resolved is not None
                    if resolved.receipt is not None:
                        return resolved.receipt
                    continue
            await asyncio.sleep(
                min(self._poll_interval, claim.lease_expires_at - now)
            )

    def _record_unclaimed_or_reuse(
        self, receipt: DeliveryReceiptV1
    ) -> DeliveryReceiptV1:
        try:
            self._journal.record_receipt(receipt, now=self._clock())
            return receipt
        except IdempotencyInvariantError:
            existing = self._journal.get(receipt.delivery_id)
            if existing is not None and existing.receipt is not None:
                return existing.receipt
            raise

    def _record_owned_or_reuse(
        self,
        receipt: DeliveryReceiptV1,
        claim: DeliveryDispatchClaim,
    ) -> DeliveryReceiptV1:
        now = self._clock()
        try:
            self._journal.record_receipt(receipt, claim=claim, now=now)
            return receipt
        except IdempotencyInvariantError:
            existing = self._journal.get(receipt.delivery_id)
            if existing is not None and existing.receipt is not None:
                return existing.receipt
            if existing is not None:
                current_claim = self._claim_from_entry(
                    receipt.delivery_id, existing
                )
                if current_claim is not None and current_claim.lease_expires_at <= now:
                    unknown = self._receipt(
                        existing.intent,
                        status=DeliveryStatus.UNKNOWN,
                        attempt=current_claim.attempt,
                        detail=(
                            "delivery owner lease expired without a durable receipt"
                        ),
                    )
                    self._journal.record_expired_receipt(
                        unknown, claim=current_claim, now=now
                    )
                    return unknown
            raise

    @staticmethod
    def _claim_from_entry(
        delivery_id: str, entry: DeliveryJournalEntry
    ) -> DeliveryDispatchClaim | None:
        if (
            entry.dispatching_attempt is None
            or entry.dispatch_owner_token is None
            or entry.dispatch_lease_expires_at is None
        ):
            return None
        return DeliveryDispatchClaim(
            delivery_id=delivery_id,
            attempt=entry.dispatching_attempt,
            owner_token=entry.dispatch_owner_token,
            fencing_token=entry.dispatch_fencing_token,
            lease_expires_at=entry.dispatch_lease_expires_at,
        )

    @staticmethod
    def _receipt(
        intent: DeliveryIntentV1,
        *,
        status: DeliveryStatus,
        attempt: int,
        external_message_id: str | None = None,
        detail: str | None = None,
    ) -> DeliveryReceiptV1:
        return DeliveryReceiptV1(
            receipt_id=f"{intent.delivery_id}:{attempt}:{status.value}",
            request_id=intent.request_id,
            delivery_id=intent.delivery_id,
            sequence=attempt,
            occurred_at=datetime.now(UTC),
            status=status,
            attempt=attempt,
            external_message_id=external_message_id,
            detail=detail,
        )


@dataclass(frozen=True, slots=True)
class PersistenceReceiptV1:
    persistence_id: str
    payload_hash: str
    persisted: bool


class InMemoryPersistenceJournal:
    def __init__(self) -> None:
        self.intents = UniquePayloadLedger()
        self.receipts: dict[str, PersistenceReceiptV1] = {}


PersistenceWriter = Callable[[str, str, str, str], None | Awaitable[None]]


class PersistenceRuntime:
    """DELIVERED 결과만 writer에 넘기며 recovery 시 delivery를 호출하지 않는다."""

    def __init__(
        self,
        *,
        journal: InMemoryPersistenceJournal,
        writer: PersistenceWriter,
    ) -> None:
        self._journal = journal
        self._writer = writer

    async def persist_delivered(
        self,
        *,
        session_key: str,
        request_id: str,
        artifact_hash: str,
        content: str,
        delivery_receipt: DeliveryReceiptV1,
    ) -> PersistenceReceiptV1 | None:
        if delivery_receipt.status is not DeliveryStatus.DELIVERED:
            return None
        identity = persistence_id(session_key, request_id, artifact_hash)
        payload_hash = _content_hash(content)
        self._journal.intents.record(identity, payload_hash)
        existing = self._journal.receipts.get(identity)
        if existing is not None:
            return existing
        result = self._writer(session_key, identity, payload_hash, content)
        if inspect.isawaitable(result):
            await result
        receipt = PersistenceReceiptV1(identity, payload_hash, True)
        self._journal.receipts[identity] = receipt
        return receipt

    @staticmethod
    def delivery_receipt_for_test(
        *, delivery_id: str, status: DeliveryStatus
    ) -> DeliveryReceiptV1:
        return DeliveryReceiptV1(
            receipt_id=f"test:{delivery_id}:{status.value}",
            request_id="request-1",
            delivery_id=delivery_id,
            sequence=1,
            occurred_at=datetime.now(UTC),
            status=status,
        )


@dataclass(frozen=True, slots=True)
class GraphDeliveryContext:
    """startup/channel 경계가 graph turn에 주입하는 delivery 식별자다."""

    channel: str
    destination_ref: str
    session_key: str
    max_attempts: int = 1
    shadow: bool = False


DeliveryContextResolver = Callable[
    [Mapping[str, object]],
    GraphDeliveryContext | Awaitable[GraphDeliveryContext],
]


class GraphCompletionRuntime:
    """core graph의 final composition부터 persistence까지 한 경로로 연결한다."""

    def __init__(
        self,
        *,
        composition: FinalCompositionRuntime,
        delivery: DeliveryRuntime,
        persistence: PersistenceRuntime,
        resolve_context: DeliveryContextResolver,
    ) -> None:
        self._composition = composition
        self._delivery = delivery
        self._persistence = persistence
        self._resolve_context = resolve_context

    def callbacks(self):
        """builder가 요구하는 production completion node callbacks를 반환한다."""
        from .nodes import CoreCompletionCallbacks

        return CoreCompletionCallbacks(
            final_composition=self.final_composition,
            prepare_delivery=self.prepare_delivery,
            commit_delivery=self.commit_delivery,
            persist_delivery_outcome=self.persist_delivery_outcome,
        )

    async def final_composition(self, state: Mapping[str, object]) -> dict[str, object]:
        result = state.get("normalized_result")
        request_id = state.get("request_id")
        outcome = state.get("terminal_outcome")
        if not isinstance(result, NormalizedAssetResultV1):
            raise TypeError("final composition requires NormalizedAssetResultV1")
        if not isinstance(request_id, str) or not request_id:
            raise TypeError("final composition requires request_id")
        if not isinstance(outcome, TerminalOutcome):
            raise TypeError("final composition requires terminal_outcome")
        final = await self._composition.finalize(
            request_id=request_id,
            normalized_result=result,
            outcome=outcome,
            composition_input=(
                state.get("composition_candidate")
                if getattr(
                    state.get("composition_candidate"), "schema_version", None
                )
                == "composition_input.v1"
                else None
            ),
        )
        return {} if final is None else {"final_artifact": final}

    async def prepare_delivery(self, state: Mapping[str, object]) -> dict[str, object]:
        from .nodes import prepare_delivery_intent

        final = state.get("final_artifact")
        if final is None:
            return {}
        if not isinstance(final, FinalArtifactV1):
            raise TypeError("delivery preparation requires FinalArtifactV1")
        context = self._resolve_context(state)
        if inspect.isawaitable(context):
            context = await context
        if not isinstance(context, GraphDeliveryContext):
            raise TypeError("delivery context resolver returned an invalid context")
        intent = prepare_delivery_intent(
            final,
            channel=context.channel,
            destination_ref=context.destination_ref,
            max_attempts=context.max_attempts,
            shadow=context.shadow,
        )
        return {"delivery_context": context, "delivery_intent": intent}

    async def commit_delivery(self, state: Mapping[str, object]) -> dict[str, object]:
        intent = state.get("delivery_intent")
        final = state.get("final_artifact")
        if intent is None or final is None:
            return {}
        if not isinstance(intent, DeliveryIntentV1) or not isinstance(
            final, FinalArtifactV1
        ):
            raise TypeError("delivery commit requires typed intent and artifact")
        receipt = await self._delivery.deliver(intent, final.content)
        return {"delivery_receipt": receipt}

    async def persist_delivery_outcome(
        self, state: Mapping[str, object]
    ) -> dict[str, object]:
        final = state.get("final_artifact")
        receipt = state.get("delivery_receipt")
        context = state.get("delivery_context")
        if final is None or receipt is None or context is None:
            return {}
        if not isinstance(final, FinalArtifactV1):
            raise TypeError("persistence requires FinalArtifactV1")
        if not isinstance(receipt, DeliveryReceiptV1):
            raise TypeError("persistence requires DeliveryReceiptV1")
        if not isinstance(context, GraphDeliveryContext):
            raise TypeError("persistence requires GraphDeliveryContext")
        persisted = await self._persistence.persist_delivered(
            session_key=context.session_key,
            request_id=final.request_id,
            artifact_hash=final.content_hash,
            content=final.content,
            delivery_receipt=receipt,
        )
        return {} if persisted is None else {"persistence_receipt": persisted}
