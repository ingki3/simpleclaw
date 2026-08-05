"""Delivery journal과 ConversationStore persistence recovery facade."""

from __future__ import annotations

import hashlib
import inspect
import math
import sqlite3
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from .adapters.delivery import DeliveryAdapter, NullDeliveryAdapter
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
    """Legacy primary와 격리 V4 shadow/canary 판정을 연결하는 rollout facade다."""

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
        if mode not in {"shadow", "canary"}:
            raise ShadowNoSendConfigurationError(
                "rollout facade supports only shadow or canary mode"
            )
        if not shadow_no_send:
            raise ShadowNoSendConfigurationError(
                "shadow/canary rollout requires shadow_no_send"
            )
        self.architecture = architecture
        self.mode = mode
        self.shadow_no_send = shadow_no_send
        self.budget = budget
        self.checkpoint_path = resolve_checkpoint_path(
            checkpoint_path,
            daemon_db_path=daemon_db_path,
            conversations_db_path=conversations_db_path,
        )

    def shadow_delivery_runtime(self, journal: DeliveryJournal) -> DeliveryRuntime:
        """Live callback을 받을 수 없는 NullDeliveryAdapter runtime만 만든다."""
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
    receipt: DeliveryReceiptV1 | None = None


class DeliveryJournal(Protocol):
    def get(self, delivery_id: str) -> DeliveryJournalEntry | None: ...
    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None: ...
    def mark_dispatching(self, delivery_id: str, *, attempt: int) -> None: ...
    def clear_failed_before_send(self, delivery_id: str) -> None: ...
    def record_receipt(self, receipt: DeliveryReceiptV1) -> None: ...


class InMemoryDeliveryJournal:
    """테스트/주입용 journal. production은 같은 API의 durable 구현을 주입한다."""

    def __init__(self) -> None:
        self._entries: dict[str, DeliveryJournalEntry] = {}

    def get(self, delivery_id: str) -> DeliveryJournalEntry | None:
        return self._entries.get(delivery_id)

    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None:
        content_hash = _content_hash(content)
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

    def mark_dispatching(self, delivery_id: str, *, attempt: int) -> None:
        entry = self._entries[delivery_id]
        self._entries[delivery_id] = DeliveryJournalEntry(
            intent=entry.intent,
            content_hash=entry.content_hash,
            dispatching_attempt=attempt,
            receipt=entry.receipt,
        )

    def clear_failed_before_send(self, delivery_id: str) -> None:
        entry = self._entries[delivery_id]
        self._entries[delivery_id] = DeliveryJournalEntry(
            intent=entry.intent, content_hash=entry.content_hash
        )

    def record_receipt(self, receipt: DeliveryReceiptV1) -> None:
        entry = self._entries[receipt.delivery_id]
        if entry.receipt is not None and entry.receipt != receipt:
            raise IdempotencyInvariantError("delivery receipt is write-once")
        self._entries[receipt.delivery_id] = DeliveryJournalEntry(
            intent=entry.intent,
            content_hash=entry.content_hash,
            receipt=receipt,
        )


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
                    receipt_json TEXT
                )
                """
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
                "SELECT intent_json, content_hash, dispatching_attempt, receipt_json "
                "FROM graph_delivery_journal WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryJournalEntry(
            intent=DeliveryIntentV1.model_validate_json(row[0]),
            content_hash=str(row[1]),
            dispatching_attempt=(int(row[2]) if row[2] is not None else None),
            receipt=(
                DeliveryReceiptV1.model_validate_json(row[3])
                if row[3] is not None
                else None
            ),
        )

    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None:
        content_hash = _content_hash(content)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT intent_json, content_hash FROM graph_delivery_journal "
                "WHERE delivery_id = ?",
                (intent.delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO graph_delivery_journal "
                    "(delivery_id, intent_json, content_hash) VALUES (?, ?, ?)",
                    (intent.delivery_id, intent.model_dump_json(), content_hash),
                )
                return
        if (
            DeliveryIntentV1.model_validate_json(row[0]) != intent
            or str(row[1]) != content_hash
        ):
            raise IdempotencyInvariantError(
                "delivery_id already exists with a different intent or payload"
            )

    def mark_dispatching(self, delivery_id: str, *, attempt: int) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = ? "
                "WHERE delivery_id = ? AND receipt_json IS NULL",
                (attempt, delivery_id),
            )
            if cursor.rowcount != 1:
                raise IdempotencyInvariantError(
                    "cannot mark missing or completed delivery as dispatching"
                )

    def clear_failed_before_send(self, delivery_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL "
                "WHERE delivery_id = ? AND receipt_json IS NULL",
                (delivery_id,),
            )

    def record_receipt(self, receipt: DeliveryReceiptV1) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM graph_delivery_journal "
                "WHERE delivery_id = ?",
                (receipt.delivery_id,),
            ).fetchone()
            if row is None:
                raise IdempotencyInvariantError("delivery intent is missing")
            if row[0] is not None:
                if DeliveryReceiptV1.model_validate_json(row[0]) != receipt:
                    raise IdempotencyInvariantError("delivery receipt is write-once")
                return
            conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL, "
                "receipt_json = ? WHERE delivery_id = ?",
                (receipt.model_dump_json(), receipt.delivery_id),
            )


class DeliveryRuntime:
    def __init__(
        self,
        *,
        journal: DeliveryJournal,
        adapters: Mapping[str, DeliveryAdapter],
    ) -> None:
        self._journal = journal
        self._adapters = dict(adapters)

    async def deliver(
        self, intent: DeliveryIntentV1, content: str
    ) -> DeliveryReceiptV1:
        self._journal.record_intent(intent, content)
        entry = self._journal.get(intent.delivery_id)
        assert entry is not None
        if entry.receipt is not None:
            return entry.receipt
        if entry.dispatching_attempt is not None:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.UNKNOWN,
                attempt=entry.dispatching_attempt,
                detail="sender started but no durable receipt was found",
            )
            self._journal.record_receipt(receipt)
            return receipt

        if intent.status is DeliveryStatus.SHADOWED:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.SHADOWED,
                attempt=1,
            )
            self._journal.record_receipt(receipt)
            return receipt

        adapter = self._adapters.get(intent.channel)
        if adapter is None:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.FAILED_BEFORE_SEND,
                attempt=1,
                detail=f"no delivery adapter for channel {intent.channel!r}",
            )
            self._journal.record_receipt(receipt)
            return receipt

        last: DeliveryReceiptV1 | None = None
        for attempt in range(1, intent.max_attempts + 1):
            self._journal.mark_dispatching(intent.delivery_id, attempt=attempt)
            result = await adapter.send(intent, content)
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
                self._journal.clear_failed_before_send(intent.delivery_id)
                continue
            self._journal.record_receipt(last)
            return last
        assert last is not None
        self._journal.record_receipt(last)
        return last

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
