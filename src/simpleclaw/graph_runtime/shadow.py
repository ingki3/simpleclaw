"""Production planner 결과를 V4 graph/no-send rollout 판정까지 연결한다."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from simpleclaw.agent.asset_result_presentation import (
    compose_user_facing_asset_result,
)
from simpleclaw.agent.resolution_types import ExecutionMode
from simpleclaw.agent.turn_plan import UnifiedTurnPlan
from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.runtime_budget import bind_runtime_llm_budget

from .adapters.base import AdapterResponse
from .adapters.native_tool import GenericNativeToolAdapter, NativeToolExecutor
from .adapters.recipe import GenericRecipeAdapter, RecipeExecutor
from .adapters.skill import GenericSkillAdapter, SkillExecutor
from .builder import compile_core_graph
from .checkpoint import resolve_checkpoint_path
from .composition import FinalCompositionRuntime
from .contracts import (
    AssetBindingRefV1,
    AssetInvocationV1,
    AssetRefV1,
    FinalArtifactV1,
    NormalizedAssetResultV1,
)
from .contracts_registry import (
    ContractAssetDefinition,
    ContractRegistryError,
    ContractRegistrySnapshotV1,
    RegistryAssetEntryV1,
    build_contract_registry,
)
from .nodes import CoreCompletionCallbacks, CoreNodeCallbacks
from .routing import (
    GeneralRoute,
    RecipeMatchOutcome,
    RecipeResultOutcome,
    SolverOutcome,
)
from .runtime import (
    CanaryGateDecisionV1,
    GraphCompletionRuntime,
    GraphDeliveryContext,
    InMemoryDeliveryJournal,
    InMemoryPersistenceJournal,
    LangGraphV4ExecutionReceiptV1,
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    PersistenceRuntime,
    ShadowBudgetUsageV1,
    ShadowComparisonTelemetryV1,
    ShadowRunTelemetryV1,
    ShadowSideEffectCountsV1,
    TargetDispatchTraceV1,
    evaluate_read_only_canary,
)
from .side_effect_monitor import capture_shadow_side_effects
from .status import (
    AssetResultStatus,
    DeliveryStatus,
    EffectStatus,
    InvocationStatus,
    TerminalOutcome,
)


@dataclass(frozen=True, slots=True)
class ConnectedShadowResultV1:
    """한 connected graph 실행의 telemetry와 mode별 receipt다."""

    telemetry: ShadowRunTelemetryV1
    comparison: ShadowComparisonTelemetryV1 | None
    canary: CanaryGateDecisionV1 | None
    side_effect_counts: ShadowSideEffectCountsV1
    execution: LangGraphV4ExecutionReceiptV1


class _ShadowBudgetStop(RuntimeError):
    """실행 전 reserve gate가 만든 typed shadow 중단 신호다."""

    def __init__(self, stop_condition: str) -> None:
        self.stop_condition = stop_condition
        super().__init__(stop_condition)


class _TargetDispatchInvariantError(RuntimeError):
    """두 번째 target helper dispatch를 실제 adapter 호출 전에 차단한다."""


ConnectedFailurePhase = Literal[
    "setup",
    "registry",
    "registry_lookup",
    "binding",
    "dispatch",
    "receipt",
]
_CONNECTED_FAILURE_PHASES = frozenset(
    {"setup", "registry", "registry_lookup", "binding", "dispatch", "receipt"}
)
_CONNECTED_ERROR_TYPES = frozenset(
    {
        "CancelledError",
        "ContractRegistryError",
        "IntegrityError",
        "InvalidStateError",
        "OperationalError",
        "OSError",
        "RuntimeError",
        "TimeoutError",
        "TypeError",
        "ValueError",
    }
)
_CONNECTED_ERROR_CODES = frozenset(
    {
        "approved_asset_fingerprint_mismatch",
        "asset_identity_missing",
        "asset_not_registered_read_only",
    }
)
_CONNECTED_ASSET_KINDS = frozenset({"recipe", "skill", "native_tool"})


def _closed_asset_kind(value: str) -> str:
    """User-managed asset identity를 closed kind로만 투영한다."""
    return value if value in _CONNECTED_ASSET_KINDS else "unknown"


def _closed_fingerprint(value: str) -> str:
    """Canonical SHA-256만 diagnostic formatter로 전달한다."""
    normalized = value.lower()
    if len(normalized) != 64:
        return ""
    if any(character not in "0123456789abcdef" for character in normalized):
        return ""
    return normalized


def _sanitized_exception_message(exc: BaseException) -> str:
    """예외 원문을 복구할 수 없는 bounded digest diagnostic으로 투영한다."""
    message = str(exc)
    digest = hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest()
    return f"message_sha256={digest[:16]}"


class ConnectedExecutionError(RuntimeError):
    """Connected 경계의 typed provenance와 sanitized cause를 보존한다."""

    def __init__(
        self,
        phase: ConnectedFailurePhase,
        cause: BaseException,
        *,
        code: str | None = None,
        selected_asset_kind: str = "none",
        selected_asset_hash: str = "",
        approved_asset_hash: str = "",
        catalog_fingerprint: str = "",
        registry_fingerprint: str = "",
        owned_input_contract_present: bool | None = None,
        owned_output_contract_present: bool | None = None,
        owned_binding_present: bool | None = None,
    ) -> None:
        self.phase: ConnectedFailurePhase = (
            phase if phase in _CONNECTED_FAILURE_PHASES else "setup"
        )
        # Provider가 임의 ``code`` attribute에 payload를 담을 수 있으므로 자동
        # 승격하지 않는다. 호출자가 정적으로 지정한 code 또는 phase code만 쓴다.
        self.code = (
            code
            if code in _CONNECTED_ERROR_CODES
            else f"connected_{self.phase}_failed"
        )
        cause_type = type(cause).__name__
        self.error_type = (
            cause_type if cause_type in _CONNECTED_ERROR_TYPES else "ExternalError"
        )
        self.safe_message = _sanitized_exception_message(cause)
        self.selected_asset_kind = (
            "none"
            if selected_asset_kind == "none"
            else _closed_asset_kind(selected_asset_kind)
        )
        self.selected_asset_hash = _closed_fingerprint(selected_asset_hash)
        self.approved_asset_hash = _closed_fingerprint(approved_asset_hash)
        self.catalog_fingerprint = _closed_fingerprint(catalog_fingerprint)
        self.registry_fingerprint = _closed_fingerprint(registry_fingerprint)
        self.owned_input_contract_present = owned_input_contract_present
        self.owned_output_contract_present = owned_output_contract_present
        self.owned_binding_present = owned_binding_present
        super().__init__(
            f"{self.phase}:{self.code}:{self.error_type}:{self.safe_message}"
        )


def _connected_error(
    phase: ConnectedFailurePhase,
    exc: BaseException,
    **diagnostic: Any,
) -> ConnectedExecutionError:
    if isinstance(exc, ConnectedExecutionError):
        return exc
    return ConnectedExecutionError(phase, exc, **diagnostic)


def _tag_connected_error(
    phase: ConnectedFailurePhase,
    exc: BaseException,
) -> BaseException:
    """기존 public exception type을 깨지 않고 phase diagnostic을 부착한다."""
    exc.connected_phase = phase if phase in _CONNECTED_FAILURE_PHASES else "setup"
    cause_type = type(exc).__name__
    exc.connected_error_type = (
        cause_type if cause_type in _CONNECTED_ERROR_TYPES else "ExternalError"
    )
    exc.connected_safe_message = _sanitized_exception_message(exc)
    return exc


@asynccontextmanager
async def _connected_checkpointer(path: str | Path):
    """SQLite setup/teardown 실패를 connected setup phase로 고정한다."""
    try:
        async with AsyncSqliteSaver.from_conn_string(str(path)) as checkpointer:
            yield checkpointer
    except ConnectedExecutionError:
        raise
    except Exception as exc:
        raise _connected_error("setup", exc) from exc


def _compile_connected_graph(callbacks, completion_callbacks, *, checkpointer):
    try:
        return compile_core_graph(
            callbacks,
            completion_callbacks,
            checkpointer=checkpointer,
        )
    except Exception as exc:
        raise _connected_error("setup", exc) from exc


DurableDispatchLifecycle = Literal[
    "not_started",
    "claimed",
    "executed",
    "terminal",
    "ambiguous",
]


@dataclass(frozen=True, slots=True)
class DurableDispatchProvenanceV1:
    """Receipt 소실 뒤에도 fallback 판단에 쓰는 durable dispatch 증거다."""

    request_id: str
    lifecycle: DurableDispatchLifecycle
    invocation_id: str | None = None

    @property
    def pre_dispatch_proven(self) -> bool:
        return self.lifecycle == "not_started"


class _TargetDispatchGuard:
    """한 graph run에서 selected invocation을 정확히 한 번만 실행한다."""

    def __init__(self, invocation: AssetInvocationV1) -> None:
        self._invocation = invocation
        self.attempted = 0
        self.executed = 0
        self.succeeded = 0
        self.duplicate_blocked = 0

    def begin(self) -> None:
        self.attempted += 1
        if self.attempted != 1:
            self.duplicate_blocked += 1
            raise _TargetDispatchInvariantError("duplicate_target_dispatch")

    def mark_executed(self) -> None:
        self.executed += 1

    def complete(self, *, succeeded: bool) -> None:
        if succeeded:
            self.succeeded += 1

    def reuse_terminal(self, *, succeeded: bool) -> None:
        """Durable terminal receipt가 증명한 전역 dispatch 횟수를 반영한다."""
        self.attempted = 1
        self.executed = 1
        self.succeeded = int(succeeded)

    def snapshot(self) -> TargetDispatchTraceV1:
        return TargetDispatchTraceV1(
            target_asset_ref=self._invocation.asset_ref,
            invocation_id=self._invocation.invocation_id,
            attempted=self.attempted,
            executed=self.executed,
            succeeded=self.succeeded,
            duplicate_blocked=self.duplicate_blocked,
        )


class _DurableInvocationClaims:
    """프로세스 재시작과 checkpoint resume를 가로지르는 dispatch claim journal."""

    def __init__(
        self,
        checkpoint_path: str | Path,
        *,
        lease_seconds: float = 30.0,
        poll_seconds: float = 0.01,
    ) -> None:
        if lease_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("claim lease and poll intervals must be positive")
        path = Path(checkpoint_path)
        self._path = path.with_name(f"{path.name}.invocations.sqlite3")
        self._owner_token = secrets.token_hex(16)
        self._lease_seconds = lease_seconds
        self._poll_seconds = poll_seconds
        self._fencing_tokens: dict[str, int] = {}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS graph_invocation_claims ("
                "invocation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
                "asset_type TEXT NOT NULL, asset_name TEXT NOT NULL, "
                "payload_hash TEXT NOT NULL, lifecycle TEXT NOT NULL, "
                "response_json TEXT, signature_json TEXT, owner_token TEXT, "
                "lease_expires_at REAL, fencing_token INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS graph_request_claims ("
                "request_id TEXT PRIMARY KEY, invocation_id TEXT NOT NULL, "
                "signature_json TEXT NOT NULL)"
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(graph_invocation_claims)")
            }
            migrations = {
                "signature_json": "TEXT",
                "owner_token": "TEXT",
                "lease_expires_at": "REAL",
                "fencing_token": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, declaration in migrations.items():
                if column not in columns:
                    conn.execute(
                        f"ALTER TABLE graph_invocation_claims "
                        f"ADD COLUMN {column} {declaration}"
                    )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=30)

    @staticmethod
    def _response_json(response: AdapterResponse) -> str:
        return json.dumps(
            {
                "invocation_id": response.invocation_id,
                "status": response.status.value,
                "input_payload_hash": response.input_payload_hash,
                "effect_status": response.effect_status.value,
                "result": (
                    response.result.model_dump(mode="json", by_alias=True)
                    if response.result is not None
                    else None
                ),
                "dispatched": response.dispatched,
                "error_code": response.error_code,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _signature_json(
        invocation: AssetInvocationV1,
        binding_ref: AssetBindingRefV1 | None,
    ) -> str:
        return json.dumps(
            {
                "asset_ref": invocation.asset_ref.model_dump(mode="json"),
                "definition_fingerprint": invocation.definition_fingerprint,
                "input_contract": invocation.input_contract.model_dump(mode="json"),
                "output_contract": invocation.output_contract.model_dump(mode="json"),
                "binding_ref": (
                    binding_ref.model_dump(mode="json")
                    if binding_ref is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _request_signature_json(
        cls,
        invocation: AssetInvocationV1,
        binding_ref: AssetBindingRefV1 | None,
    ) -> str:
        return json.dumps(
            {
                "invocation_id": invocation.invocation_id,
                "asset_ref": invocation.asset_ref.model_dump(mode="json"),
                "definition_fingerprint": invocation.definition_fingerprint,
                "input_payload": invocation.payload,
                "input_payload_hash": invocation.payload_hash,
                "input_contract": invocation.input_contract.model_dump(mode="json"),
                "output_contract": invocation.output_contract.model_dump(mode="json"),
                "binding_ref": (
                    binding_ref.model_dump(mode="json")
                    if binding_ref is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def _ensure_request_claim(
        cls,
        conn: sqlite3.Connection,
        request_id: str,
        invocation: AssetInvocationV1,
        binding_ref: AssetBindingRefV1 | None,
        *,
        create: bool,
    ) -> bool:
        """request 전체의 최초 execution signature를 immutable하게 고정한다."""
        signature_json = cls._request_signature_json(invocation, binding_ref)
        row = conn.execute(
            "SELECT invocation_id, signature_json FROM graph_request_claims "
            "WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is not None:
            if tuple(row) != (invocation.invocation_id, signature_json):
                error_code = (
                    "invocation_identity_mismatch"
                    if str(row[0]) == invocation.invocation_id
                    else "request_identity_mismatch"
                )
                raise _TargetDispatchInvariantError(error_code)
            return True

        invocation_signature_json = cls._signature_json(invocation, binding_ref)
        historical = conn.execute(
            "SELECT invocation_id, request_id, asset_type, asset_name, "
            "payload_hash, signature_json FROM graph_invocation_claims "
            "WHERE request_id = ? ORDER BY invocation_id",
            (request_id,),
        ).fetchall()
        identity = cls._identity(request_id, invocation)
        if historical:
            same_invocation = (
                len(historical) == 1
                and str(historical[0][0]) == invocation.invocation_id
            )
            if (
                not same_invocation
                or tuple(historical[0][1:5]) != identity
                or historical[0][5] != invocation_signature_json
            ):
                error_code = (
                    "invocation_identity_mismatch"
                    if same_invocation
                    else "request_identity_mismatch"
                )
                raise _TargetDispatchInvariantError(error_code)
        elif not create:
            return False

        conn.execute(
            "INSERT INTO graph_request_claims "
            "(request_id, invocation_id, signature_json) VALUES (?, ?, ?)",
            (request_id, invocation.invocation_id, signature_json),
        )
        return True

    @staticmethod
    def _validate_response(
        invocation: AssetInvocationV1,
        response: AdapterResponse,
    ) -> AdapterResponse:
        if response.invocation_id != invocation.invocation_id:
            raise _TargetDispatchInvariantError("response_invocation_mismatch")
        if response.input_payload_hash != invocation.payload_hash:
            raise _TargetDispatchInvariantError("response_input_hash_mismatch")
        result = response.result
        if result is not None:
            if result.invocation_id != invocation.invocation_id:
                raise _TargetDispatchInvariantError("result_invocation_mismatch")
            if result.output_contract != invocation.output_contract:
                raise _TargetDispatchInvariantError("result_contract_mismatch")
            if result.status is not response.status:
                raise _TargetDispatchInvariantError("result_status_mismatch")
            if result.effect_status is not response.effect_status:
                raise _TargetDispatchInvariantError("result_effect_mismatch")
            canonical = json.dumps(
                result.payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != (
                result.payload_hash
            ):
                raise _TargetDispatchInvariantError("result_payload_hash_mismatch")
        if response.status is AssetResultStatus.RESOLVED:
            if result is None or not response.dispatched:
                raise _TargetDispatchInvariantError("response_provenance_mismatch")
        elif result is not None and result.status is AssetResultStatus.RESOLVED:
            raise _TargetDispatchInvariantError("response_status_mismatch")
        return response

    @classmethod
    def _response(
        cls,
        raw: str,
        invocation: AssetInvocationV1,
    ) -> AdapterResponse:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {
                "invocation_id",
                "status",
                "input_payload_hash",
                "effect_status",
                "result",
                "dispatched",
                "error_code",
            }:
                raise ValueError("non-canonical response shape")
            result = value["result"]
            response = AdapterResponse(
                invocation_id=value["invocation_id"],
                status=AssetResultStatus(value["status"]),
                input_payload_hash=value["input_payload_hash"],
                effect_status=EffectStatus(value["effect_status"]),
                result=(
                    NormalizedAssetResultV1.model_validate_json(
                        json.dumps(
                            result,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    if result is not None
                    else None
                ),
                dispatched=value["dispatched"],
                receipt_reused=True,
                error_code=value["error_code"],
            )
            if not isinstance(response.dispatched, bool):
                raise TypeError("dispatched must be boolean")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _TargetDispatchInvariantError("corrupt_terminal_response") from exc
        return cls._validate_response(invocation, response)

    @staticmethod
    def _identity(request_id: str, invocation: AssetInvocationV1) -> tuple[str, ...]:
        return (
            request_id,
            invocation.asset_ref.type,
            invocation.asset_ref.name,
            invocation.payload_hash,
        )

    async def claim(
        self,
        request_id: str,
        invocation: AssetInvocationV1,
        *,
        binding_ref: AssetBindingRefV1 | None = None,
    ) -> AdapterResponse | None:
        identity = self._identity(request_id, invocation)
        signature_json = self._signature_json(invocation, binding_ref)
        while True:
            now = time.time()
            expired_executed = False
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._ensure_request_claim(
                    conn,
                    request_id,
                    invocation,
                    binding_ref,
                    create=True,
                )
                row = conn.execute(
                    "SELECT request_id, asset_type, asset_name, payload_hash, "
                    "lifecycle, response_json, signature_json, owner_token, "
                    "lease_expires_at, fencing_token FROM graph_invocation_claims "
                    "WHERE invocation_id = ?",
                    (invocation.invocation_id,),
                ).fetchone()
                if row is None:
                    fencing_token = 1
                    conn.execute(
                        "INSERT INTO graph_invocation_claims "
                        "(invocation_id, request_id, asset_type, asset_name, "
                        "payload_hash, lifecycle, response_json, signature_json, "
                        "owner_token, lease_expires_at, fencing_token) "
                        "VALUES (?, ?, ?, ?, ?, 'claimed', NULL, ?, ?, ?, ?)",
                        (
                            invocation.invocation_id,
                            *identity,
                            signature_json,
                            self._owner_token,
                            now + self._lease_seconds,
                            fencing_token,
                        ),
                    )
                    self._fencing_tokens[invocation.invocation_id] = fencing_token
                    return None
                if tuple(row[:4]) != identity or row[6] != signature_json:
                    raise _TargetDispatchInvariantError("invocation_identity_mismatch")
                lifecycle, response_json = str(row[4]), row[5]
                if lifecycle == "terminal" and isinstance(response_json, str):
                    return self._response(response_json, invocation)
                if lifecycle == "ambiguous":
                    raise _TargetDispatchInvariantError("manual_recovery_required")
                lease_expires_at = float(row[8] or 0.0)
                fencing_token = int(row[9])
                if row[7] == self._owner_token:
                    raise _TargetDispatchInvariantError("claim_already_owned")
                if lease_expires_at <= now:
                    if lifecycle != "claimed":
                        cursor = conn.execute(
                            "UPDATE graph_invocation_claims SET lifecycle = 'ambiguous', "
                            "owner_token = NULL, lease_expires_at = NULL, "
                            "fencing_token = fencing_token + 1 "
                            "WHERE invocation_id = ? AND lifecycle = ? "
                            "AND fencing_token = ? AND lease_expires_at <= ?",
                            (
                                invocation.invocation_id,
                                lifecycle,
                                fencing_token,
                                now,
                            ),
                        )
                        if cursor.rowcount == 1:
                            expired_executed = True
                    else:
                        cursor = conn.execute(
                            "UPDATE graph_invocation_claims SET owner_token = ?, "
                            "lease_expires_at = ?, fencing_token = fencing_token + 1 "
                            "WHERE invocation_id = ? AND lifecycle = 'claimed' "
                            "AND fencing_token = ? AND lease_expires_at <= ?",
                            (
                                self._owner_token,
                                now + self._lease_seconds,
                                invocation.invocation_id,
                                fencing_token,
                                now,
                            ),
                        )
                        if cursor.rowcount == 1:
                            self._fencing_tokens[invocation.invocation_id] = (
                                fencing_token + 1
                            )
                            return None
            if expired_executed:
                raise _TargetDispatchInvariantError("manual_recovery_required")
            await asyncio.sleep(self._poll_seconds)

    def terminal(
        self,
        request_id: str,
        invocation: AssetInvocationV1,
        *,
        binding_ref: AssetBindingRefV1 | None = None,
    ) -> AdapterResponse | None:
        """Checkpoint가 callback을 생략한 resume에서 terminal receipt를 읽는다."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not self._ensure_request_claim(
                conn,
                request_id,
                invocation,
                binding_ref,
                create=False,
            ):
                return None
            row = conn.execute(
                "SELECT request_id, asset_type, asset_name, payload_hash, "
                "lifecycle, response_json, signature_json FROM graph_invocation_claims "
                "WHERE invocation_id = ?",
                (invocation.invocation_id,),
            ).fetchone()
        if row is None:
            return None
        identity = self._identity(request_id, invocation)
        if tuple(row[:4]) != identity:
            raise _TargetDispatchInvariantError("invocation_identity_mismatch")
        if row[6] != self._signature_json(invocation, binding_ref):
            raise _TargetDispatchInvariantError("invocation_identity_mismatch")
        if row[4] == "terminal" and isinstance(row[5], str):
            return self._response(row[5], invocation)
        return None

    def mark_executed(self, invocation_id: str) -> None:
        fencing_token = self._fencing_tokens.get(invocation_id)
        if fencing_token is None:
            raise _TargetDispatchInvariantError("claim_not_owned")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'executed', "
                "lease_expires_at = ? WHERE invocation_id = ? "
                "AND lifecycle = 'claimed' AND owner_token = ? "
                "AND fencing_token = ?",
                (
                    time.time() + self._lease_seconds,
                    invocation_id,
                    self._owner_token,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise _TargetDispatchInvariantError("claim_not_dispatchable")

    async def renew_lease(self, invocation_id: str) -> None:
        """실행 owner가 끝날 때까지 같은 fencing token의 lease만 갱신한다."""
        fencing_token = self._fencing_tokens.get(invocation_id)
        if fencing_token is None:
            raise _TargetDispatchInvariantError("claim_not_owned")
        interval = max(0.001, self._lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE graph_invocation_claims SET lease_expires_at = ? "
                    "WHERE invocation_id = ? AND lifecycle = 'executed' "
                    "AND owner_token = ? AND fencing_token = ? "
                    "AND lease_expires_at > ?",
                    (
                        now + self._lease_seconds,
                        invocation_id,
                        self._owner_token,
                        fencing_token,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _TargetDispatchInvariantError("claim_lease_lost")

    def mark_terminal(
        self,
        invocation: AssetInvocationV1,
        response: AdapterResponse,
    ) -> None:
        self._validate_response(invocation, response)
        fencing_token = self._fencing_tokens.get(invocation.invocation_id)
        if fencing_token is None:
            raise _TargetDispatchInvariantError("claim_not_owned")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'terminal', "
                "response_json = ?, lease_expires_at = NULL "
                "WHERE invocation_id = ? AND lifecycle = 'executed' "
                "AND owner_token = ? AND fencing_token = ?",
                (
                    self._response_json(response),
                    response.invocation_id,
                    self._owner_token,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise _TargetDispatchInvariantError("claim_not_executed")

    def mark_ambiguous(self, invocation_id: str) -> None:
        fencing_token = self._fencing_tokens.get(invocation_id)
        if fencing_token is None:
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'ambiguous', "
                "lease_expires_at = NULL WHERE invocation_id = ? "
                "AND lifecycle != 'terminal' AND owner_token = ? "
                "AND fencing_token = ?",
                (
                    invocation_id,
                    self._owner_token,
                    fencing_token,
                ),
            )

    def provenance(self, request_id: str) -> DurableDispatchProvenanceV1:
        """request의 durable claim 상태를 receipt와 독립적으로 반환한다."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT invocation_id, lifecycle FROM graph_invocation_claims "
                "WHERE request_id = ? ORDER BY invocation_id",
                (request_id,),
            ).fetchall()
        if not rows:
            return DurableDispatchProvenanceV1(
                request_id=request_id,
                lifecycle="not_started",
            )
        if len(rows) != 1:
            return DurableDispatchProvenanceV1(
                request_id=request_id,
                lifecycle="ambiguous",
            )
        invocation_id, lifecycle = str(rows[0][0]), str(rows[0][1])
        if lifecycle not in {
            "claimed",
            "executed",
            "terminal",
            "ambiguous",
        }:
            lifecycle = "ambiguous"
        return DurableDispatchProvenanceV1(
            request_id=request_id,
            lifecycle=cast(DurableDispatchLifecycle, lifecycle),
            invocation_id=invocation_id,
        )


def load_durable_dispatch_provenance(
    checkpoint_path: str | Path,
    request_id: str,
) -> DurableDispatchProvenanceV1:
    """Production orchestrator가 receipt-loss 예외 뒤 journal을 직접 조회한다."""
    if not str(checkpoint_path):
        raise ValueError("checkpoint path is required for dispatch provenance")
    return _DurableInvocationClaims(
        resolve_checkpoint_path(checkpoint_path)
    ).provenance(request_id)


class _ShadowRunBudget:
    """한 connected run의 deadline과 소비 축을 실행 전에 원자적으로 예약한다."""

    def __init__(
        self,
        limits: ShadowBudgetUsageV1,
        *,
        planner_model_calls: int,
        planner_tokens: int,
    ) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (planner_model_calls, planner_tokens)
        ):
            raise ValueError("planner usage must use non-negative integers")
        self._limits = limits
        self._started = time.perf_counter()
        self.graph_steps = 0
        self.asset_calls = 0
        self.llm_calls = planner_model_calls
        self.tokens = planner_tokens
        self.parallel_active = 0
        self.parallel_peak = 0
        self._next_llm_ticket = 0
        self._llm_reservations: dict[int, int] = {}

    @property
    def elapsed_seconds(self) -> float:
        return time.perf_counter() - self._started

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._limits.max_seconds - self.elapsed_seconds)

    def _reserve_gate(self) -> None:
        if self.elapsed_seconds >= self._limits.max_seconds:
            raise _ShadowBudgetStop("deadline")
        if any(
            (
                self.graph_steps >= self._limits.max_graph_steps,
                self.asset_calls >= self._limits.max_asset_calls,
                self.llm_calls >= self._limits.max_llm_calls,
                self.tokens >= self._limits.max_tokens,
            )
        ):
            raise _ShadowBudgetStop("budget_exhausted")

    def reserve_graph_step(self) -> None:
        """다음 graph callback이 시작되기 전에 step을 예약한다."""
        self._reserve_gate()
        self.graph_steps += 1

    def reserve_asset_call(self) -> None:
        """executor가 시작되기 전에 asset/parallel slot을 예약한다."""
        self._reserve_gate()
        if self.parallel_active >= self._limits.max_parallel_invocations:
            raise _ShadowBudgetStop("budget_exhausted")
        self.asset_calls += 1
        self.parallel_active += 1
        self.parallel_peak = max(self.parallel_peak, self.parallel_active)

    def release_asset_call(self) -> None:
        if self.parallel_active <= 0:
            raise RuntimeError("shadow asset reservation is not active")
        self.parallel_active -= 1

    def reserve_llm_call(self, max_tokens: int | None) -> tuple[int, object]:
        """provider 호출 전에 LLM call과 output-token cap을 함께 예약한다."""
        self._reserve_gate()
        if self.parallel_active >= self._limits.max_parallel_invocations:
            raise _ShadowBudgetStop("budget_exhausted")
        reserved = sum(self._llm_reservations.values())
        remaining_tokens = self._limits.max_tokens - self.tokens - reserved
        if remaining_tokens <= 0:
            raise _ShadowBudgetStop("budget_exhausted")
        if max_tokens is not None and (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens <= 0
        ):
            raise ValueError("LLM max_tokens must be a positive integer")
        capped_tokens = min(max_tokens or remaining_tokens, remaining_tokens)
        self.llm_calls += 1
        self.parallel_active += 1
        self.parallel_peak = max(self.parallel_peak, self.parallel_active)
        self._next_llm_ticket += 1
        ticket = self._next_llm_ticket
        self._llm_reservations[ticket] = capped_tokens
        return capped_tokens, ticket

    def complete_llm_call(
        self,
        ticket: object,
        usage: Mapping[str, object] | None,
    ) -> None:
        """provider reported output token을 기록하고 미보고 시 예약량을 보수적으로 쓴다."""
        if not isinstance(ticket, int) or ticket not in self._llm_reservations:
            raise RuntimeError("unknown shadow LLM reservation")
        reserved = self._llm_reservations.pop(ticket)
        raw_tokens = usage.get("output_tokens") if usage is not None else None
        actual_tokens = (
            raw_tokens
            if isinstance(raw_tokens, int)
            and not isinstance(raw_tokens, bool)
            and raw_tokens >= 0
            else reserved
        )
        self.tokens += actual_tokens
        if self.parallel_active <= 0:
            raise RuntimeError("shadow LLM reservation is not active")
        self.parallel_active -= 1

    def usage(self, stop_condition: str) -> ShadowBudgetUsageV1:
        return ShadowBudgetUsageV1(
            max_graph_steps=self._limits.max_graph_steps,
            max_asset_calls=self._limits.max_asset_calls,
            max_llm_calls=self._limits.max_llm_calls,
            max_tokens=self._limits.max_tokens,
            max_seconds=self._limits.max_seconds,
            max_parallel_invocations=self._limits.max_parallel_invocations,
            graph_steps=self.graph_steps,
            asset_calls=self.asset_calls,
            llm_calls=self.llm_calls,
            tokens=self.tokens,
            elapsed_seconds=self.elapsed_seconds,
            parallel_peak=self.parallel_peak,
            stop_condition=stop_condition,
        )


def _budgeted_node(callback, budget: _ShadowRunBudget):
    """LangGraph callback의 실제 본문보다 먼저 step budget을 예약한다."""

    async def node(state):
        budget.reserve_graph_step()
        update = callback(state)
        if inspect.isawaitable(update):
            update = await update
        return update

    return node


def _budgeted_resume(callback, budget: _ShadowRunBudget):
    async def node(state, control):
        budget.reserve_graph_step()
        update = callback(state, control)
        if inspect.isawaitable(update):
            update = await update
        return update

    return node


def _question_payload(
    registry: ContractRegistrySnapshotV1,
    entry: RegistryAssetEntryV1,
    question: str,
) -> dict[str, Any]:
    """질문을 우선 검증하고 계약이 선언한 안전한 fallback만 사용한다."""
    schema = entry.input_descriptor.json_schema
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        raise TypeError("shadow input contract must declare object properties")
    if not required or any(
        not isinstance(name, str)
        or properties.get(name, {}).get("type") != "string"
        for name in required
    ):
        raise ValueError("shadow input contract requires explicit string fields")

    candidates: list[Mapping[str, Any]] = [{name: question for name in required}]
    declared_default = schema.get("default")
    if isinstance(declared_default, Mapping):
        candidates.append(declared_default)
    declared_examples = schema.get("examples")
    if isinstance(declared_examples, list):
        candidates.extend(
            item for item in declared_examples if isinstance(item, Mapping)
        )

    property_fallback: dict[str, Any] = {}
    for name in required:
        field = properties[name]
        if "default" in field:
            property_fallback[name] = field["default"]
            continue
        examples = field.get("examples")
        if isinstance(examples, list) and examples:
            property_fallback[name] = examples[0]
    if len(property_fallback) == len(required):
        candidates.append(property_fallback)

    for candidate in candidates:
        try:
            return registry.validate_canonical(
                entry.input_descriptor, candidate
            ).payload
        except ContractRegistryError:
            continue
    raise ContractRegistryError("payload.safe_example_missing")


def _route_for_plan(plan: UnifiedTurnPlan, asset_ref: AssetRefV1) -> str:
    if asset_ref.type == "recipe":
        return "recipe"
    if plan.execution.mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM:
        return "deep_research"
    return "react"


def _compose_user_facing_result(result: NormalizedAssetResultV1) -> str:
    """Core 밖 generic presentation boundary의 text 결과만 소비한다."""
    return compose_user_facing_asset_result(
        payload=result.payload,
        result_status=result.status.value,
        effect_status=result.effect_status.value,
    )


def _invocation_status(response: AdapterResponse) -> InvocationStatus:
    if response.status is AssetResultStatus.RESOLVED and response.result is not None:
        return InvocationStatus.SUCCEEDED
    if response.effect_status is EffectStatus.UNKNOWN:
        return InvocationStatus.UNKNOWN_EFFECT
    if response.effect_status is EffectStatus.PARTIAL:
        return InvocationStatus.PARTIAL_EFFECT
    return InvocationStatus.FAILED_TERMINAL


class ConnectedShadowTurnRunner:
    """설정→facade→V4 graph→telemetry→canary를 한 production 경계로 실행한다."""

    def __init__(
        self,
        *,
        facade: LangGraphV4RolloutFacade,
        definitions: Sequence[ContractAssetDefinition],
        conversation_store: object,
        recipe_executor: RecipeExecutor | None = None,
        skill_executor: SkillExecutor | None = None,
        native_tool_executor: NativeToolExecutor | None = None,
    ) -> None:
        self._facade = facade
        self._definitions = tuple(definitions)
        try:
            self._registry = build_contract_registry(self._definitions)
        except ContractRegistryError as exc:
            _tag_connected_error("registry", exc)
            raise
        except Exception as exc:
            raise _connected_error("registry", exc) from exc
        self._conversation_store = conversation_store
        self._recipe_executor = recipe_executor
        self._skill_executor = skill_executor
        self._native_tool_executor = native_tool_executor

    async def run(
        self,
        *,
        plan: UnifiedTurnPlan,
        legacy: LegacyRunTelemetryV1 | None,
        request_id: str,
        session_key: str,
        planner_model_calls: int,
        planner_tokens: int,
    ) -> ConnectedShadowResultV1:
        """PlanGate가 승인한 exact asset을 graph completion 끝까지 no-send 실행한다."""
        selected = plan.capability.primary_asset
        if selected is None:
            exc = ValueError("connected shadow requires a planner-selected asset")
            raise _connected_error(
                "registry_lookup",
                exc,
                code="asset_identity_missing",
                catalog_fingerprint=plan.catalog_fingerprint,
                registry_fingerprint=self._registry.fingerprint,
            ) from exc
        asset_ref = AssetRefV1(type=selected.asset_type, name=selected.name)
        definition_matches = tuple(
            item
            for item in self._definitions
            if item.contract_asset_type == asset_ref.type
            and item.name == asset_ref.name
        )
        definition = definition_matches[0] if len(definition_matches) == 1 else None
        input_present = bool(
            definition is not None and definition.input_contract is not None
        )
        output_present = bool(
            definition is not None and definition.output_contract is not None
        )
        binding_present = bool(
            definition is not None and definition.contract_binding is not None
        )
        definition_fingerprint = (
            definition.definition_fingerprint if definition is not None else ""
        )
        diagnostic = {
            "selected_asset_kind": asset_ref.type,
            "selected_asset_hash": definition_fingerprint,
            "approved_asset_hash": plan.approved_asset_fingerprint,
            "catalog_fingerprint": plan.catalog_fingerprint,
            "registry_fingerprint": self._registry.fingerprint,
            "owned_input_contract_present": input_present,
            "owned_output_contract_present": output_present,
            "owned_binding_present": binding_present,
        }
        entry = self._registry.asset(asset_ref)
        if (
            entry is None
            or not entry.snapshot.read_only
            or entry.snapshot.side_effects
        ):
            exc = ValueError("connected shadow asset must be registered read-only")
            raise _connected_error(
                "registry_lookup",
                exc,
                code="asset_not_registered_read_only",
                **diagnostic,
            ) from exc
        if (
            not plan.approved_asset_fingerprint
            or plan.approved_asset_fingerprint != definition_fingerprint
            or plan.approved_asset_fingerprint
            != entry.snapshot.definition_fingerprint
        ):
            exc = ValueError("approved asset definition fingerprint mismatch")
            raise _connected_error(
                "registry_lookup",
                exc,
                code="approved_asset_fingerprint_mismatch",
                **diagnostic,
            ) from exc
        try:
            if definition is None:
                raise ValueError("connected definition identity must resolve exactly once")
            if (
                definition.definition_fingerprint
                != entry.snapshot.definition_fingerprint
            ):
                raise ValueError("connected definition fingerprint mismatch")
            if entry.snapshot.declared_binding is None:
                raise ValueError("connected definition binding is missing")
            payload = _question_payload(
                self._registry,
                entry,
                plan.context.standalone_question,
            )
            canonical = self._registry.validate_canonical(
                entry.input_descriptor, payload
            )
        except Exception as exc:
            raise _connected_error("binding", exc, **diagnostic) from exc
        invocation = AssetInvocationV1(
            invocation_id=hashlib.sha256(
                f"{request_id}:{asset_ref.type}:{asset_ref.name}".encode()
            ).hexdigest(),
            asset_ref=asset_ref,
            definition_fingerprint=entry.snapshot.definition_fingerprint,
            input_contract=entry.input_descriptor.ref,
            payload=canonical.payload,
            payload_hash=canonical.payload_hash,
            output_contract=entry.output_descriptor.ref,
        )
        if asset_ref.type == "recipe":
            adapter = GenericRecipeAdapter(
                self._registry,
                definition,
                executor=self._recipe_executor,
            )
        elif asset_ref.type == "native_tool":
            adapter = GenericNativeToolAdapter(
                self._registry,
                definition,
                executor=self._native_tool_executor,
            )
        else:
            adapter = GenericSkillAdapter(
                self._registry,
                definition,
                executor=self._skill_executor,
            )
        route = _route_for_plan(plan, asset_ref)
        response: AdapterResponse | None = None
        durable_terminal_reused = False
        dispatch_guard = _TargetDispatchGuard(invocation)
        durable_claims = _DurableInvocationClaims(self._facade.checkpoint_path)
        binding_ref = entry.snapshot.declared_binding
        budget_controller = _ShadowRunBudget(
            self._facade.budget,
            planner_model_calls=planner_model_calls,
            planner_tokens=planner_tokens,
        )

        async def dispatch(_state: Mapping[str, object]) -> dict[str, object]:
            nonlocal durable_terminal_reused, response
            dispatch_guard.begin()
            budget_controller.reserve_asset_call()
            try:
                reused = await durable_claims.claim(
                    request_id,
                    invocation,
                    binding_ref=binding_ref,
                )
                if reused is not None:
                    assert reused.result is not None
                    if reused.status is AssetResultStatus.RESOLVED:
                        canonical_result = self._registry.validate_canonical(
                            entry.output_descriptor,
                            reused.result.payload,
                        )
                        if canonical_result.payload_hash != reused.result.payload_hash:
                            raise _TargetDispatchInvariantError(
                                "result_payload_hash_mismatch"
                            )
                    response = reused
                    durable_terminal_reused = True
                    dispatch_guard.reuse_terminal(
                        succeeded=(
                            response.status is AssetResultStatus.RESOLVED
                            and response.result is not None
                            and response.effect_status
                            in {EffectStatus.NONE, EffectStatus.VERIFIED}
                        )
                    )
                else:
                    durable_claims.mark_executed(invocation.invocation_id)
                    dispatch_guard.mark_executed()
                    heartbeat = asyncio.create_task(
                        durable_claims.renew_lease(invocation.invocation_id)
                    )
                    try:
                        response = await adapter.dispatch(invocation)
                    except BaseException:
                        durable_claims.mark_ambiguous(invocation.invocation_id)
                        raise
                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat
            finally:
                budget_controller.release_asset_call()
            if response.result is None:
                failed_result = NormalizedAssetResultV1(
                    invocation_id=invocation.invocation_id,
                    output_contract=invocation.output_contract,
                    status=response.status,
                    payload={},
                    payload_hash=hashlib.sha256(b"{}").hexdigest(),
                    effect_status=response.effect_status,
                )
                response = replace(response, result=failed_result)
            if not response.receipt_reused:
                assert response.result is not None
                if response.status is AssetResultStatus.RESOLVED:
                    canonical_result = self._registry.validate_canonical(
                        entry.output_descriptor,
                        response.result.payload,
                    )
                    if canonical_result.payload_hash != response.result.payload_hash:
                        raise _TargetDispatchInvariantError(
                            "result_payload_hash_mismatch"
                        )
                durable_claims.mark_terminal(invocation, response)
                dispatch_guard.complete(
                    succeeded=(
                        response.status is AssetResultStatus.RESOLVED
                        and response.result is not None
                        and response.effect_status
                        in {EffectStatus.NONE, EffectStatus.VERIFIED}
                    )
                )
            update: dict[str, object] = {
                "invocation": invocation,
                "invocation_status": _invocation_status(response),
                "asset_result_status": response.status,
                "effect_status": response.effect_status,
            }
            update["normalized_result"] = response.result
            return update

        def assess(_state: Mapping[str, object]) -> dict[str, object]:
            assert response is not None
            safe_result = (
                response.status is AssetResultStatus.RESOLVED
                and response.effect_status in {EffectStatus.NONE, EffectStatus.VERIFIED}
            )
            if route == "recipe":
                outcome = (
                    RecipeResultOutcome.RESOLVED
                    if safe_result
                    else RecipeResultOutcome.UNKNOWN_EFFECT
                )
                return {"recipe_result": outcome}
            outcome = (
                SolverOutcome.RESOLVED
                if safe_result
                else SolverOutcome.FAILED
            )
            return {"solver_outcome": outcome}

        def no_op(_state: Mapping[str, object]) -> dict[str, object]:
            return {}

        raw_callbacks = CoreNodeCallbacks(
            normalize_ingress=lambda _state: {"request_id": request_id},
            load_existing_context=no_op,
            analyze_request=no_op,
            snapshot_asset_catalogs=lambda _state: {
                "catalog": self._registry.fingerprint
            },
            match_recipe=lambda _state: {
                "recipe_match": (
                    RecipeMatchOutcome.APPLICABLE
                    if route == "recipe"
                    else RecipeMatchOutcome.NO_MATCH
                )
            },
            execute_existing_recipe=dispatch,
            assess_recipe_result=assess,
            select_general_route=lambda _state: {
                "general_route": (
                    GeneralRoute.DEEP_RESEARCH
                    if route == "deep_research"
                    else GeneralRoute.REACT
                )
            },
            simple_conversation=dispatch,
            react_subgraph=dispatch,
            assess_react_result=assess,
            deep_research_subgraph=dispatch,
            assess_deep_research_result=assess,
            compose_candidate=lambda _state: {
                "composition_candidate": "shadow",
                "terminal_outcome": (
                    TerminalOutcome.COMPLETED
                    if response is not None
                    and response.status is AssetResultStatus.RESOLVED
                    and response.effect_status
                    in {EffectStatus.NONE, EffectStatus.VERIFIED}
                    else TerminalOutcome.FAILED
                ),
            },
            resume_user_input=lambda _state, _control: {},
        )
        callbacks = CoreNodeCallbacks(
            normalize_ingress=_budgeted_node(
                raw_callbacks.normalize_ingress, budget_controller
            ),
            load_existing_context=_budgeted_node(
                raw_callbacks.load_existing_context, budget_controller
            ),
            analyze_request=_budgeted_node(
                raw_callbacks.analyze_request, budget_controller
            ),
            snapshot_asset_catalogs=_budgeted_node(
                raw_callbacks.snapshot_asset_catalogs, budget_controller
            ),
            match_recipe=_budgeted_node(
                raw_callbacks.match_recipe, budget_controller
            ),
            execute_existing_recipe=_budgeted_node(
                raw_callbacks.execute_existing_recipe, budget_controller
            ),
            assess_recipe_result=_budgeted_node(
                raw_callbacks.assess_recipe_result, budget_controller
            ),
            select_general_route=_budgeted_node(
                raw_callbacks.select_general_route, budget_controller
            ),
            simple_conversation=_budgeted_node(
                raw_callbacks.simple_conversation, budget_controller
            ),
            react_subgraph=_budgeted_node(
                raw_callbacks.react_subgraph, budget_controller
            ),
            assess_react_result=_budgeted_node(
                raw_callbacks.assess_react_result, budget_controller
            ),
            deep_research_subgraph=_budgeted_node(
                raw_callbacks.deep_research_subgraph, budget_controller
            ),
            assess_deep_research_result=_budgeted_node(
                raw_callbacks.assess_deep_research_result, budget_controller
            ),
            compose_candidate=_budgeted_node(
                raw_callbacks.compose_candidate, budget_controller
            ),
            resume_user_input=_budgeted_resume(
                raw_callbacks.resume_user_input, budget_controller
            ),
        )
        composition = FinalCompositionRuntime(
            compose=_compose_user_facing_result,
            guard=lambda content: bool(content.strip())
            and not content.lstrip().startswith(("{", "[")),
            safe_render=lambda _result: (
                "요청을 처리했지만 안전한 응답을 구성하지 못했습니다."
            ),
        )
        completion = GraphCompletionRuntime(
            composition=composition,
            delivery=self._facade.shadow_delivery_runtime(
                InMemoryDeliveryJournal()
            ),
            persistence=PersistenceRuntime(
                journal=InMemoryPersistenceJournal(),
                writer=ConversationStorePersistenceAdapter(
                    self._conversation_store,
                    channel="shadow",
                ),
            ),
            resolve_context=lambda _state: GraphDeliveryContext(
                channel="telegram",
                destination_ref="shadow:no-send",
                session_key=session_key,
                shadow=True,
            ),
        )
        raw_completion = completion.callbacks()
        completion_callbacks = CoreCompletionCallbacks(
            final_composition=_budgeted_node(
                raw_completion.final_composition, budget_controller
            ),
            prepare_delivery=_budgeted_node(
                raw_completion.prepare_delivery, budget_controller
            ),
            commit_delivery=_budgeted_node(
                raw_completion.commit_delivery, budget_controller
            ),
            persist_delivery_outcome=_budgeted_node(
                raw_completion.persist_delivery_outcome, budget_controller
            ),
        )
        Path(self._facade.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        state: Mapping[str, object] = {}
        stop_condition = "completed"
        failure_code: str | None = None
        async with _connected_checkpointer(self._facade.checkpoint_path) as checkpointer:
            graph = _compile_connected_graph(
                callbacks,
                completion_callbacks,
                checkpointer=checkpointer,
            )
            with (
                capture_shadow_side_effects() as monitor,
                bind_runtime_llm_budget(budget_controller),
            ):
                try:
                    async with asyncio.timeout(
                        budget_controller.remaining_seconds
                    ):
                        state = await graph.ainvoke(
                            {"ingress": plan.context.standalone_question},
                            {
                                "configurable": {
                                    "thread_id": f"shadow:{request_id}"
                                },
                                "recursion_limit": (
                                    self._facade.budget.max_graph_steps + 1
                                ),
                            },
                        )
                except TimeoutError:
                    stop_condition = "deadline"
                except _ShadowBudgetStop as exc:
                    stop_condition = exc.stop_condition
                except _TargetDispatchInvariantError as exc:
                    stop_condition = "blocked"
                    failure_code = str(exc)
                except Exception as exc:  # noqa: BLE001 - typed rollback boundary
                    stop_condition = "failed"
                    failure_code = str(_connected_error("dispatch", exc))
        if response is None:
            try:
                response = durable_claims.terminal(
                    request_id,
                    invocation,
                    binding_ref=binding_ref,
                )
                if response is not None:
                    assert response.result is not None
                    if response.status is AssetResultStatus.RESOLVED:
                        canonical_result = self._registry.validate_canonical(
                            entry.output_descriptor,
                            response.result.payload,
                        )
                        if canonical_result.payload_hash != response.result.payload_hash:
                            raise _TargetDispatchInvariantError(
                                "result_payload_hash_mismatch"
                            )
                    durable_terminal_reused = True
            except (ContractRegistryError, _TargetDispatchInvariantError) as exc:
                stop_condition = "blocked"
                failure_code = str(exc)
                response = None
                state = {
                    key: value
                    for key, value in state.items()
                    if key
                    not in {
                        "final_artifact",
                        "delivery_intent",
                        "delivery_receipt",
                        "persistence_receipt",
                    }
                }
                state = {
                    **state,
                    "terminal_outcome": TerminalOutcome.BLOCKED,
                }
        if durable_terminal_reused and response is not None:
            safe_terminal = (
                response.status is AssetResultStatus.RESOLVED
                and response.result is not None
                and response.effect_status in {EffectStatus.NONE, EffectStatus.VERIFIED}
            )
            dispatch_guard.reuse_terminal(succeeded=safe_terminal)
            if safe_terminal:
                # 이미 durable terminal인 호출은 checkpoint serializer/resume 오류로
                # 완료 판정을 뒤집거나 target을 재실행하지 않는다.
                stop_condition = "completed"
                failure_code = None
                final_artifact = await composition.finalize(
                    request_id=request_id,
                    normalized_result=response.result,
                    outcome=TerminalOutcome.COMPLETED,
                )
                if final_artifact is None:
                    stop_condition = "blocked"
                    failure_code = "final_composition_rejected"
                else:
                    state = {
                        **state,
                        "final_artifact": final_artifact,
                        "terminal_outcome": TerminalOutcome.COMPLETED,
                    }
        if stop_condition != "completed" and response is None:
            stopped_status = (
                AssetResultStatus.FAILED
                if stop_condition == "deadline"
                else AssetResultStatus.BLOCKED
            )
            stopped_effect = EffectStatus.NONE
            stopped_result = NormalizedAssetResultV1(
                invocation_id=invocation.invocation_id,
                output_contract=invocation.output_contract,
                status=stopped_status,
                payload={},
                payload_hash=hashlib.sha256(b"{}").hexdigest(),
                effect_status=stopped_effect,
            )
            response = AdapterResponse(
                invocation_id=invocation.invocation_id,
                status=stopped_status,
                input_payload_hash=invocation.payload_hash,
                effect_status=stopped_effect,
                result=stopped_result,
                error_code=failure_code or stop_condition,
            )
        if response is None or response.result is None:
            raise RuntimeError("connected shadow graph did not produce a typed result")
        delivery_receipt = state.get("delivery_receipt")
        delivery_status = (
            DeliveryStatus.SHADOWED
            if durable_terminal_reused
            else getattr(
                delivery_receipt,
                "status",
                DeliveryStatus.NOT_READY,
            )
        )
        budget = budget_controller.usage(stop_condition)
        dispatch_trace = dispatch_guard.snapshot()
        invocation_status = _invocation_status(response)
        terminal_outcome = state.get("terminal_outcome", TerminalOutcome.FAILED)
        if stop_condition == "deadline":
            invocation_status = InvocationStatus.TIMED_OUT
            terminal_outcome = TerminalOutcome.TIMED_OUT
        elif stop_condition == "budget_exhausted":
            if response.error_code == "budget_exhausted":
                invocation_status = InvocationStatus.DENIED
            terminal_outcome = TerminalOutcome.BLOCKED
        telemetry = ShadowRunTelemetryV1.from_contract_run(
            run_id=request_id,
            request_id=request_id,
            checkpoint_thread_id=f"shadow:{request_id}",
            plan_id=hashlib.sha256(repr(plan).encode()).hexdigest(),
            plan_revision=1,
            catalog_fingerprint=self._registry.fingerprint,
            entry=entry,
            invocation=invocation,
            selected_route=route,
            invocation_status=invocation_status,
            result=response.result,
            effect_status=response.effect_status,
            terminal_outcome=terminal_outcome,
            delivery_status=delivery_status,
            budget_usage=budget,
            model_call_attribution={"planner": planner_model_calls, "composer": 0},
            dispatch_trace=dispatch_trace,
        )
        counts = ShadowSideEffectCountsV1(
            telegram_send=monitor.telegram_send,
            conversation_write=monitor.conversation_write,
            notifier=monitor.notifier,
        )
        comparison = (
            self._facade.compare(
                legacy,
                telemetry,
                side_effect_counts=counts,
            )
            if legacy is not None
            else None
        )
        canary = (
            evaluate_read_only_canary(comparison, [entry.snapshot])
            if comparison is not None
            else None
        )
        final_artifact = state.get("final_artifact")
        if final_artifact is not None and not isinstance(
            final_artifact, FinalArtifactV1
        ):
            raise TypeError("connected graph returned an invalid final artifact")
        rollback_reasons: list[str] = []
        if stop_condition != "completed":
            rollback_reasons.append(failure_code or stop_condition)
        if not dispatch_trace.exactly_once:
            rollback_reasons.append("target_dispatch_not_exactly_once")
        if invocation_status is not InvocationStatus.SUCCEEDED:
            rollback_reasons.append("invocation_not_succeeded")
        if response.status is not AssetResultStatus.RESOLVED:
            rollback_reasons.append("asset_result_not_resolved")
        if response.effect_status not in {EffectStatus.NONE, EffectStatus.VERIFIED}:
            rollback_reasons.append("effect_not_safe")
        if terminal_outcome is not TerminalOutcome.COMPLETED:
            rollback_reasons.append("graph_not_completed")
        if final_artifact is None:
            rollback_reasons.append("typed_final_missing")
        if delivery_status is not DeliveryStatus.SHADOWED:
            rollback_reasons.append("graph_delivery_not_deferred")
        if counts.total:
            rollback_reasons.append("external_side_effect")
        unique_reasons = tuple(dict.fromkeys(rollback_reasons))
        try:
            execution = LangGraphV4ExecutionReceiptV1(
                mode=self._facade.mode,
                request_id=request_id,
                selected_route=route,
                final_artifact=final_artifact,
                dispatch_trace=dispatch_trace,
                budget_usage=budget,
                side_effect_counts=counts,
                terminal_outcome=terminal_outcome,
                rollback_required=bool(unique_reasons),
                rollback_reasons=unique_reasons,
                effect_status=response.effect_status,
            )
        except Exception as exc:
            raise _connected_error("receipt", exc) from exc
        return ConnectedShadowResultV1(
            telemetry,
            comparison,
            canary,
            counts,
            execution,
        )
