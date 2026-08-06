"""Production planner 결과를 V4 graph/no-send rollout 판정까지 연결한다."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
from .composition import FinalCompositionRuntime
from .contracts import (
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

    def __init__(self, checkpoint_path: str | Path) -> None:
        path = Path(checkpoint_path)
        self._path = path.with_name(f"{path.name}.invocations.sqlite3")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS graph_invocation_claims ("
                "invocation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
                "asset_type TEXT NOT NULL, asset_name TEXT NOT NULL, "
                "payload_hash TEXT NOT NULL, lifecycle TEXT NOT NULL, "
                "response_json TEXT)"
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
    def _response(raw: str) -> AdapterResponse:
        value = json.loads(raw)
        result = value["result"]
        if isinstance(result, dict):
            result = {
                **result,
                "status": AssetResultStatus(result["status"]),
                "effect_status": EffectStatus(result["effect_status"]),
            }
        return AdapterResponse(
            invocation_id=value["invocation_id"],
            status=AssetResultStatus(value["status"]),
            input_payload_hash=value["input_payload_hash"],
            effect_status=EffectStatus(value["effect_status"]),
            result=(
                NormalizedAssetResultV1.model_validate(result)
                if result is not None
                else None
            ),
            dispatched=bool(value["dispatched"]),
            receipt_reused=True,
            error_code=value["error_code"],
        )

    def claim(self, request_id: str, invocation: AssetInvocationV1) -> AdapterResponse | None:
        identity = (
            request_id,
            invocation.asset_ref.type,
            invocation.asset_ref.name,
            invocation.payload_hash,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT request_id, asset_type, asset_name, payload_hash, "
                "lifecycle, response_json FROM graph_invocation_claims "
                "WHERE invocation_id = ?",
                (invocation.invocation_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO graph_invocation_claims VALUES (?, ?, ?, ?, ?, ?, NULL)",
                    (invocation.invocation_id, *identity, "claimed"),
                )
                return None
            if tuple(row[:4]) != identity:
                raise _TargetDispatchInvariantError("invocation_identity_mismatch")
            lifecycle, response_json = str(row[4]), row[5]
            if lifecycle == "terminal" and isinstance(response_json, str):
                return self._response(response_json)
            conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'ambiguous' "
                "WHERE invocation_id = ?",
                (invocation.invocation_id,),
            )
        raise _TargetDispatchInvariantError("manual_recovery_required")

    def terminal(
        self, request_id: str, invocation: AssetInvocationV1
    ) -> AdapterResponse | None:
        """Checkpoint가 callback을 생략한 resume에서 terminal receipt를 읽는다."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_id, asset_type, asset_name, payload_hash, "
                "lifecycle, response_json FROM graph_invocation_claims "
                "WHERE invocation_id = ?",
                (invocation.invocation_id,),
            ).fetchone()
        if row is None:
            return None
        identity = (
            request_id,
            invocation.asset_ref.type,
            invocation.asset_ref.name,
            invocation.payload_hash,
        )
        if tuple(row[:4]) != identity:
            raise _TargetDispatchInvariantError("invocation_identity_mismatch")
        if row[4] == "terminal" and isinstance(row[5], str):
            return self._response(row[5])
        return None

    def mark_executed(self, invocation_id: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'executed' "
                "WHERE invocation_id = ? AND lifecycle = 'claimed'",
                (invocation_id,),
            )
            if cursor.rowcount != 1:
                raise _TargetDispatchInvariantError("claim_not_dispatchable")

    def mark_terminal(self, response: AdapterResponse) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'terminal', "
                "response_json = ? WHERE invocation_id = ? "
                "AND lifecycle = 'executed'",
                (self._response_json(response), response.invocation_id),
            )
            if cursor.rowcount != 1:
                raise _TargetDispatchInvariantError("claim_not_executed")

    def mark_ambiguous(self, invocation_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_invocation_claims SET lifecycle = 'ambiguous' "
                "WHERE invocation_id = ? AND lifecycle != 'terminal'",
                (invocation_id,),
            )


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
    """Opaque contract JSON을 그대로 노출하지 않는 bounded deterministic renderer."""
    payload = result.payload
    preferred = ("answer", "result", "content", "text", "message", "summary")
    for key in preferred:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    strings = [value.strip() for value in payload.values() if isinstance(value, str) and value.strip()]
    if len(strings) == 1:
        return strings[0]
    lines = [
        f"- {key}: {value}"
        for key, value in sorted(payload.items())
        if isinstance(value, (str, int, float, bool))
    ]
    if lines:
        return "처리 결과입니다.\n" + "\n".join(lines)
    return "요청을 처리했지만 안전하게 표시할 수 있는 텍스트 결과가 없습니다."


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
        self._registry = build_contract_registry(self._definitions)
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
            raise ValueError("connected shadow requires a planner-selected asset")
        asset_ref = AssetRefV1(type=selected.asset_type, name=selected.name)
        entry = self._registry.asset(asset_ref)
        if entry is None or not entry.snapshot.read_only or entry.snapshot.side_effects:
            raise ValueError("connected shadow asset must be registered read-only")
        payload = _question_payload(
            self._registry,
            entry,
            plan.context.standalone_question,
        )
        canonical = self._registry.validate_canonical(entry.input_descriptor, payload)
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
        definition = next(
            item
            for item in self._definitions
            if item.contract_asset_type == asset_ref.type and item.name == asset_ref.name
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
                reused = durable_claims.claim(request_id, invocation)
                if reused is not None:
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
                    try:
                        response = await adapter.dispatch(invocation)
                    except BaseException:
                        durable_claims.mark_ambiguous(invocation.invocation_id)
                        raise
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
                durable_claims.mark_terminal(response)
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
        completion = GraphCompletionRuntime(
            composition=FinalCompositionRuntime(
                compose=_compose_user_facing_result,
                guard=lambda content: bool(content.strip()) and not content.lstrip().startswith(("{", "[")),
                safe_render=lambda _result: (
                    "요청을 처리했지만 안전한 응답을 구성하지 못했습니다."
                ),
            ),
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
        async with AsyncSqliteSaver.from_conn_string(
            str(self._facade.checkpoint_path)
        ) as checkpointer:
            graph = compile_core_graph(
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
                    failure_code = f"graph_{type(exc).__name__}"
        if response is None:
            response = durable_claims.terminal(request_id, invocation)
            if response is not None:
                durable_terminal_reused = True
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
                assert response.result is not None
                content = _compose_user_facing_result(response.result)
                artifact_id = hashlib.sha256(
                    f"artifact.v1\x1f{request_id}\x1f{content}".encode("utf-8")
                ).hexdigest()
                final_artifact = FinalArtifactV1(
                    artifact_id=artifact_id,
                    request_id=request_id,
                    content=content,
                    outcome=TerminalOutcome.COMPLETED,
                    content_hash=hashlib.sha256(
                        f"content.v1\x1f{content}".encode("utf-8")
                    ).hexdigest(),
                )
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
        return ConnectedShadowResultV1(
            telemetry,
            comparison,
            canary,
            counts,
            execution,
        )
