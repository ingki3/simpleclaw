"""Production planner 결과를 V4 graph/no-send rollout 판정까지 연결한다."""

from __future__ import annotations

import hashlib
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
from .nodes import CoreNodeCallbacks
from .routing import GeneralRoute, RecipeMatchOutcome, RecipeResultOutcome, SolverOutcome
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
        raise ValueError("shadow input contract must declare object properties")
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
        graph_steps = 0

        def step(update: Mapping[str, object] | None = None) -> dict[str, object]:
            nonlocal graph_steps
            graph_steps += 1
            return dict(update or {})

        async def dispatch(_state: Mapping[str, object]) -> dict[str, object]:
            nonlocal response
            graph_steps_before = graph_steps
            response = await adapter.dispatch(invocation)
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
            # async callback도 정확히 한 graph step으로 계측한다.
            assert graph_steps == graph_steps_before
            return step(update)

        def assess(_state: Mapping[str, object]) -> dict[str, object]:
            assert response is not None
            if route == "recipe":
                outcome = (
                    RecipeResultOutcome.RESOLVED
                    if response.status is AssetResultStatus.RESOLVED
                    else RecipeResultOutcome.UNKNOWN_EFFECT
                )
                return step({"recipe_result": outcome})
            outcome = (
                SolverOutcome.RESOLVED
                if response.status is AssetResultStatus.RESOLVED
                else SolverOutcome.FAILED
            )
            return step({"solver_outcome": outcome})

        def no_op(_state: Mapping[str, object]) -> dict[str, object]:
            return step()

        callbacks = CoreNodeCallbacks(
            normalize_ingress=lambda _state: step({"request_id": request_id}),
            load_existing_context=no_op,
            analyze_request=no_op,
            snapshot_asset_catalogs=lambda _state: step(
                {"catalog": self._registry.fingerprint}
            ),
            match_recipe=lambda _state: step(
                {
                    "recipe_match": (
                        RecipeMatchOutcome.APPLICABLE
                        if route == "recipe"
                        else RecipeMatchOutcome.NO_MATCH
                    )
                }
            ),
            execute_existing_recipe=dispatch,
            assess_recipe_result=assess,
            select_general_route=lambda _state: step(
                {
                    "general_route": (
                        GeneralRoute.DEEP_RESEARCH
                        if route == "deep_research"
                        else GeneralRoute.REACT
                    )
                }
            ),
            simple_conversation=dispatch,
            react_subgraph=dispatch,
            assess_react_result=assess,
            deep_research_subgraph=dispatch,
            assess_deep_research_result=assess,
            compose_candidate=lambda _state: step(
                {
                    "composition_candidate": "shadow",
                    "terminal_outcome": (
                        TerminalOutcome.COMPLETED
                        if response is not None
                        and response.status is AssetResultStatus.RESOLVED
                        else TerminalOutcome.FAILED
                    ),
                }
            ),
            resume_user_input=lambda _state, _control: step(),
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
        started = time.perf_counter()
        Path(self._facade.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(
            str(self._facade.checkpoint_path)
        ) as checkpointer:
            graph = compile_core_graph(
                callbacks,
                completion.callbacks(),
                checkpointer=checkpointer,
            )
            with capture_shadow_side_effects() as monitor:
                state = await graph.ainvoke(
                    {"ingress": plan.context.standalone_question},
                    {"configurable": {"thread_id": f"shadow:{request_id}"}},
                )
        elapsed = time.perf_counter() - started
        if response is None or not isinstance(
            state.get("normalized_result"), NormalizedAssetResultV1
        ):
            raise RuntimeError("connected shadow graph did not produce a typed result")
        delivery_receipt = state.get("delivery_receipt")
        delivery_status = getattr(
            delivery_receipt,
            "status",
            DeliveryStatus.NOT_READY,
        )
        budget = ShadowBudgetUsageV1(
            max_graph_steps=self._facade.budget.max_graph_steps,
            max_asset_calls=self._facade.budget.max_asset_calls,
            max_llm_calls=self._facade.budget.max_llm_calls,
            max_tokens=self._facade.budget.max_tokens,
            max_seconds=self._facade.budget.max_seconds,
            max_parallel_invocations=self._facade.budget.max_parallel_invocations,
            graph_steps=graph_steps,
            asset_calls=1,
            llm_calls=planner_model_calls,
            tokens=planner_tokens,
            elapsed_seconds=elapsed,
            parallel_peak=1,
            stop_condition="completed",
        )
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
            invocation_status=_invocation_status(response),
            result=response.result,
            effect_status=response.effect_status,
            terminal_outcome=state.get("terminal_outcome", TerminalOutcome.FAILED),
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
