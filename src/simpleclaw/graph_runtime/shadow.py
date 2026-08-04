"""Production planner 결과를 V4 graph/no-send rollout 판정까지 연결한다."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
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
from .adapters.recipe import GenericRecipeAdapter, RecipeExecutor
from .adapters.skill import GenericSkillAdapter, SkillExecutor
from .builder import compile_core_graph
from .composition import FinalCompositionRuntime
from .contracts import AssetInvocationV1, AssetRefV1, NormalizedAssetResultV1
from .contracts_registry import (
    ContractAssetDefinition,
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
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    PersistenceRuntime,
    ShadowBudgetUsageV1,
    ShadowComparisonTelemetryV1,
    ShadowRunTelemetryV1,
    ShadowSideEffectCountsV1,
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
    """한 production shadow graph 실행의 telemetry와 rollout 결론이다."""

    telemetry: ShadowRunTelemetryV1
    comparison: ShadowComparisonTelemetryV1
    canary: CanaryGateDecisionV1
    side_effect_counts: ShadowSideEffectCountsV1


class _ShadowBudgetStop(RuntimeError):
    """실행 전 reserve gate가 만든 typed shadow 중단 신호다."""

    def __init__(self, stop_condition: str) -> None:
        self.stop_condition = stop_condition
        super().__init__(stop_condition)


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
                self.parallel_peak >= self._limits.max_parallel_invocations,
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
    """단일 string 입력 계약만 의미 재해석 없이 planner 질문에 결합한다."""
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
    payload = {name: question for name in required}
    return registry.validate_canonical(entry.input_descriptor, payload).payload


def _route_for_plan(plan: UnifiedTurnPlan, asset_ref: AssetRefV1) -> str:
    if asset_ref.type == "recipe":
        return "recipe"
    if plan.execution.mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM:
        return "deep_research"
    return "react"


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
    ) -> None:
        self._facade = facade
        self._definitions = tuple(definitions)
        self._registry = build_contract_registry(self._definitions)
        self._conversation_store = conversation_store
        self._recipe_executor = recipe_executor
        self._skill_executor = skill_executor

    async def run(
        self,
        *,
        plan: UnifiedTurnPlan,
        legacy: LegacyRunTelemetryV1,
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
        adapter = (
            GenericRecipeAdapter(
                self._registry,
                definition,
                executor=self._recipe_executor,
            )
            if asset_ref.type == "recipe"
            else GenericSkillAdapter(
                self._registry,
                definition,
                executor=self._skill_executor,
            )
        )
        route = _route_for_plan(plan, asset_ref)
        response: AdapterResponse | None = None
        budget_controller = _ShadowRunBudget(
            self._facade.budget,
            planner_model_calls=planner_model_calls,
            planner_tokens=planner_tokens,
        )

        async def dispatch(_state: Mapping[str, object]) -> dict[str, object]:
            nonlocal response
            budget_controller.reserve_asset_call()
            try:
                response = await adapter.dispatch(invocation)
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
            if route == "recipe":
                outcome = (
                    RecipeResultOutcome.RESOLVED
                    if response.status is AssetResultStatus.RESOLVED
                    else RecipeResultOutcome.UNKNOWN_EFFECT
                )
                return {"recipe_result": outcome}
            outcome = (
                SolverOutcome.RESOLVED
                if response.status is AssetResultStatus.RESOLVED
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
                compose=lambda result: json.dumps(
                    result.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                guard=lambda _content: True,
                safe_render=lambda _result: "shadow-result-unavailable",
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
                error_code=stop_condition,
            )
        if response is None or response.result is None:
            raise RuntimeError("connected shadow graph did not produce a typed result")
        delivery_receipt = state.get("delivery_receipt")
        delivery_status = getattr(
            delivery_receipt,
            "status",
            DeliveryStatus.NOT_READY,
        )
        budget = budget_controller.usage(stop_condition)
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
        )
        counts = ShadowSideEffectCountsV1(
            telegram_send=monitor.telegram_send,
            conversation_write=monitor.conversation_write,
            notifier=monitor.notifier,
        )
        comparison = self._facade.compare(
            legacy,
            telemetry,
            side_effect_counts=counts,
        )
        canary = evaluate_read_only_canary(comparison, [entry.snapshot])
        return ConnectedShadowResultV1(telemetry, comparison, canary, counts)
