"""LangGraph V4 shadow/no-send와 read-only canary gate 회귀."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.resolution_types import CapabilityCoverage, ExecutionMode
from simpleclaw.agent.turn_plan import (
    AssetRef,
    CapabilityPlan,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.graph_runtime.adapters.delivery import (
    CronDeliveryAdapter,
    SenderReceipt,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.graph_runtime.contracts import (
    AssetInvocationV1,
    AssetRefV1,
    DeliveryIntentV1,
    FinalArtifactV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.runtime import (
    InMemoryDeliveryJournal,
    InMemoryPersistenceJournal,
    LangGraphV4ExecutionReceiptV1,
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    PersistenceRuntime,
    ShadowBudgetUsageV1,
    ShadowNoSendConfigurationError,
    ShadowRunTelemetryV1,
    ShadowSideEffectCountsV1,
    TargetDispatchTraceV1,
    compare_shadow_run,
    evaluate_read_only_canary,
)
from simpleclaw.graph_runtime.shadow import (
    ConnectedShadowTurnRunner,
    _ShadowBudgetStop,
    _ShadowRunBudget,
    _TargetDispatchGuard,
    _TargetDispatchInvariantError,
)
from simpleclaw.graph_runtime.side_effect_monitor import capture_shadow_side_effects
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    DeliveryStatus,
    EffectStatus,
    InvocationStatus,
    TerminalOutcome,
)
from simpleclaw.langgraph_v4_shadow_validation import (
    _definitions as _connected_validation_definitions,
)
from simpleclaw.llm.models import BackendType, LLMBackend, LLMRequest, LLMResponse
from simpleclaw.llm.router import LLMRouter
from simpleclaw.memory import ConversationStore
from simpleclaw.agent.turn_state import TurnExecutionState
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

REPO_ROOT = Path(__file__).parents[2]


def _registry():
    recipes = discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    skills = discover_skills(
        REPO_ROOT / "tests/fixtures/skills",
        REPO_ROOT / "tests/fixtures/global-skills",
    )
    definitions = [
        item
        for item in [*recipes, *skills]
        if item.name in {"contract-fixture-workflow", "contract-fixture-step"}
    ]
    return build_contract_registry(definitions)


def _definitions():
    recipes = discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    skills = discover_skills(
        REPO_ROOT / "tests/fixtures/skills",
        REPO_ROOT / "tests/fixtures/global-skills",
    )
    return tuple(
        item
        for item in (*recipes, *skills)
        if item.name in {"contract-fixture-workflow", "contract-fixture-step"}
    )


def test_connected_validation_discovers_planner_visible_contract_fixtures() -> None:
    """live harness가 repo 밖을 보며 빈 catalog를 만드는 회귀를 차단한다."""
    definitions = _connected_validation_definitions()
    identities = {
        (item.contract_asset_type, item.name) for item in definitions
    }

    assert identities == {
        ("recipe", "contract-fixture-workflow"),
        ("skill", "contract-fixture-step"),
    }
    catalog = build_planner_catalog(
        skills=tuple(
            item for item in definitions if item.contract_asset_type == "skill"
        ),
        recipes=tuple(
            item for item in definitions if item.contract_asset_type == "recipe"
        ),
        native_specs=(),
    )
    fixture_assets = {
        (asset.asset_type, asset.name): asset
        for asset in catalog.assets
        if asset.name in {"contract-fixture-workflow", "contract-fixture-step"}
    }
    assert set(fixture_assets) == identities
    assert all(asset.coverage == "full_coverage" for asset in fixture_assets.values())
    assert all(asset.input_contract == "query.v1" for asset in fixture_assets.values())
    assert all(
        asset.output_contract == "asset_result.v1"
        for asset in fixture_assets.values()
    )


def _plan(asset_type: str, name: str, mode: ExecutionMode) -> UnifiedTurnPlan:
    asset = AssetRef(asset_type=asset_type, name=name)
    return UnifiedTurnPlan(
        original_text="connected shadow fixture",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="connected-shadow-value",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("fixture",),
        intents=("verify",),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="fixture",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=asset,
            allowed_assets=(asset,),
        ),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.PARTIAL,
            primary_asset=asset,
            supporting_assets=(asset,),
        ),
        confidence=1.0,
        decision_summary="connected fixture",
    )


def _invocation(registry) -> AssetInvocationV1:
    owner = AssetRefV1(type="recipe", name="contract-fixture-workflow")
    entry = registry.asset(owner)
    assert entry is not None
    canonical = registry.validate_canonical(
        entry.input_descriptor,
        {"fixture_key": "shadow-value"},
    )
    return AssetInvocationV1(
        invocation_id="shadow-invocation-1",
        asset_ref=owner,
        definition_fingerprint=entry.snapshot.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )


def _budget(*, stop_condition: str = "completed") -> ShadowBudgetUsageV1:
    return ShadowBudgetUsageV1(
        max_graph_steps=40,
        max_asset_calls=12,
        max_llm_calls=8,
        max_tokens=16000,
        max_seconds=180,
        max_parallel_invocations=3,
        graph_steps=12,
        asset_calls=1,
        llm_calls=2,
        tokens=1200,
        elapsed_seconds=4.5,
        parallel_peak=1,
        stop_condition=stop_condition,
    )


def _budget_with(**changes) -> ShadowBudgetUsageV1:
    return replace(_budget(), graph_steps=0, asset_calls=0, llm_calls=0, tokens=0,
                   elapsed_seconds=0, parallel_peak=0, **changes)


def test_parallel_budget_releases_slot_for_sequential_graph_step() -> None:
    """동시성 1은 첫 호출 종료 후 다음 순차 graph step을 허용해야 한다."""
    budget = _ShadowRunBudget(
        _budget_with(max_parallel_invocations=1),
        planner_model_calls=0,
        planner_tokens=0,
    )

    budget.reserve_asset_call()
    budget.release_asset_call()
    budget.reserve_graph_step()

    assert budget.parallel_active == 0
    assert budget.parallel_peak == 1
    assert budget.graph_steps == 1


def test_parallel_budget_releases_slot_for_next_sequential_asset_call() -> None:
    """high-water mark가 limit에 닿아도 slot 해제 후 순차 호출은 가능하다."""
    budget = _ShadowRunBudget(
        _budget_with(max_parallel_invocations=1),
        planner_model_calls=0,
        planner_tokens=0,
    )

    budget.reserve_asset_call()
    budget.release_asset_call()
    budget.reserve_asset_call()

    assert budget.asset_calls == 2
    assert budget.parallel_active == 1
    assert budget.parallel_peak == 1


@pytest.mark.parametrize("reservation", ["asset", "llm"])
def test_parallel_budget_rejects_work_while_active_slot_is_occupied(
    reservation,
) -> None:
    """실제 active slot이 limit에 도달한 동안 추가 호출은 시작하지 않는다."""
    budget = _ShadowRunBudget(
        _budget_with(max_parallel_invocations=1),
        planner_model_calls=0,
        planner_tokens=0,
    )
    budget.reserve_asset_call()

    with pytest.raises(_ShadowBudgetStop, match="budget_exhausted"):
        if reservation == "asset":
            budget.reserve_asset_call()
        else:
            budget.reserve_llm_call(max_tokens=100)

    assert budget.asset_calls == 1
    assert budget.llm_calls == 0
    assert budget.parallel_active == 1
    assert budget.parallel_peak == 1


def _connected_runner(tmp_path, *, budget, recipe_executor):
    store = ConversationStore(tmp_path / "budget-conversation.db")
    return ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="shadow",
            shadow_no_send=True,
            budget=budget,
            checkpoint_path=tmp_path / "budget-checkpoint.sqlite3",
            daemon_db_path=tmp_path / "daemon.db",
            conversations_db_path=tmp_path / "budget-conversation.db",
        ),
        definitions=_definitions(),
        conversation_store=store,
        recipe_executor=recipe_executor,
    )


def test_target_dispatch_guard_blocks_second_attempt_before_execution() -> None:
    guard = _TargetDispatchGuard(_invocation(_registry()))

    guard.begin()
    guard.mark_executed()
    guard.complete(succeeded=True)
    with pytest.raises(
        _TargetDispatchInvariantError,
        match="duplicate_target_dispatch",
    ):
        guard.begin()

    trace = guard.snapshot()
    assert trace.attempted == 2
    assert trace.executed == 1
    assert trace.succeeded == 1
    assert trace.duplicate_blocked == 1
    assert trace.exactly_once is False


@pytest.mark.asyncio
async def test_connected_primary_returns_v4_typed_final_and_exact_dispatch(
    tmp_path,
) -> None:
    calls = 0
    store = ConversationStore(tmp_path / "primary-conversation.db")

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "primary-source"}

    runner = ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget_with(),
            checkpoint_path=tmp_path / "primary-checkpoint.sqlite3",
        ),
        definitions=_definitions(),
        conversation_store=store,
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=None,
        request_id="primary-result-source",
        session_key="primary-session",
        planner_model_calls=1,
        planner_tokens=10,
    )

    assert calls == 1
    assert result.comparison is None
    assert result.canary is None
    assert result.execution.result_source == "langgraph_v4"
    assert result.execution.final_content == '{"fixture_result":"primary-source"}'
    assert result.execution.provenance.startswith(
        "langgraph_v4:recipe:contract-fixture-workflow:"
    )
    assert result.execution.dispatch_trace.exactly_once is True
    assert result.execution.side_effect_counts.total == 0
    assert result.execution.rollback_required is False
    assert store.get_recent() == []


@pytest.mark.asyncio
async def test_connected_primary_rejects_zero_dispatch_before_typed_final(
    tmp_path,
) -> None:
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "unreachable"}

    runner = ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget_with(max_graph_steps=1),
            checkpoint_path=tmp_path / "zero-checkpoint.sqlite3",
        ),
        definitions=_definitions(),
        conversation_store=ConversationStore(tmp_path / "zero-conversation.db"),
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=None,
        request_id="zero-dispatch",
        session_key="primary-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    assert calls == 0
    assert result.execution.dispatch_trace.attempted == 0
    assert result.execution.dispatch_trace.executed == 0
    assert result.execution.final_content is None
    assert result.execution.rollback_required is True
    assert "target_dispatch_not_exactly_once" in result.execution.rollback_reasons


@pytest.mark.asyncio
async def test_connected_primary_provider_failure_is_typed_before_delivery(
    tmp_path,
) -> None:
    async def executor(_definition, _bound_steps):
        raise RuntimeError("provider unavailable")

    runner = ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget_with(),
            checkpoint_path=tmp_path / "failure-checkpoint.sqlite3",
        ),
        definitions=_definitions(),
        conversation_store=ConversationStore(tmp_path / "failure-conversation.db"),
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=None,
        request_id="provider-failure",
        session_key="primary-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    assert result.execution.dispatch_trace.executed == 1
    assert result.execution.dispatch_trace.succeeded == 0
    assert result.execution.final_content is None
    assert result.execution.side_effect_counts.total == 0
    assert result.execution.rollback_required is True
    assert "invocation_not_succeeded" in result.execution.rollback_reasons


@pytest.mark.asyncio
async def test_orchestrator_primary_response_source_is_v4_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    definitions = _definitions()
    recipes = tuple(
        item for item in definitions if item.contract_asset_type == "recipe"
    )
    skills = tuple(
        item for item in definitions if item.contract_asset_type == "skill"
    )
    planned = _plan(
        "recipe",
        "contract-fixture-workflow",
        ExecutionMode.DIRECT_ANSWER,
    )
    planned = replace(
        planned,
        capability=replace(
            planned.capability,
            coverage=CapabilityCoverage.FULL,
        ),
    )

    async def fake_planner(_text, *, catalog, **_kwargs):
        return replace(planned, catalog_fingerprint=catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._unified_turn_planner_config = {
        "architecture": "langgraph_v4",
        "mode": "primary",
        "context_candidate_limit": 8,
        "context_candidate_max_chars": 6000,
        "selected_context_max_turns": 3,
        "selected_context_max_chars": 2400,
        "max_tokens": 2048,
        "telemetry": {"enabled": False},
        "langgraph_v4": {
            "budget_valid": True,
            "on_failure": "fail_closed",
        },
    }
    orchestrator._router = object()
    orchestrator._cron_scheduler = None
    orchestrator._browser_handoff_config = {"enabled": False}
    orchestrator._recipes = recipes
    orchestrator._store = ConversationStore(tmp_path / "orchestrator-primary.db")
    orchestrator._structured_logger = None
    orchestrator._exposable_skills = MethodType(lambda self: list(skills), orchestrator)
    called = 0

    async def execute_v4(self, **kwargs):
        nonlocal called
        called += 1
        request_id = kwargs["request_id"]
        invocation_id = "orchestrator-v4-invocation"
        receipt = LangGraphV4ExecutionReceiptV1(
            mode="primary",
            request_id=request_id,
            selected_route="recipe",
            final_artifact=FinalArtifactV1(
                artifact_id="v4-artifact",
                request_id=request_id,
                content="V4가 만든 최종 응답",
                outcome=TerminalOutcome.COMPLETED,
                content_hash="v4-content-hash",
            ),
            dispatch_trace=TargetDispatchTraceV1(
                target_asset_ref=AssetRefV1(
                    type="recipe",
                    name="contract-fixture-workflow",
                ),
                invocation_id=invocation_id,
                attempted=1,
                executed=1,
                succeeded=1,
            ),
            budget_usage=_budget(),
            side_effect_counts=ShadowSideEffectCountsV1(),
            terminal_outcome=TerminalOutcome.COMPLETED,
            rollback_required=False,
            rollback_reasons=(),
        )
        return SimpleNamespace(execution=receipt)

    async def legacy_must_not_run(self, *_args, **_kwargs):
        raise AssertionError("legacy/tool-loop result was used as V4 primary")

    orchestrator._execute_langgraph_v4_connected = MethodType(
        execute_v4,
        orchestrator,
    )
    orchestrator._run_tool_loop_result = MethodType(
        legacy_must_not_run,
        orchestrator,
    )
    turn = TurnExecutionState.create(
        session_key="orchestrator-primary-session",
        original_text=planned.original_text,
    )

    result = await orchestrator._run_unified_turn_planner_primary(
        planned.original_text,
        recent_rows=[],
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        operator_tools=False,
        turn=turn,
    )

    assert called == 1
    assert result.text == "V4가 만든 최종 응답"
    assert result.success is True
    assert result.selected_route == "recipe"
    assert turn.final_text == result.text
    assert turn.phase.value == "completed"


@pytest.mark.asyncio
async def test_shadow_uses_null_delivery_and_never_calls_live_side_effects(
    tmp_path,
) -> None:
    calls = {"telegram": 0, "conversation": 0, "notifier": 0}

    async def live_sender(_destination, _content):
        calls["telegram"] += 1

    async def live_notifier(_destination, _content):
        calls["notifier"] += 1

    async def live_writer(_session, _identity, _payload_hash, _content):
        calls["conversation"] += 1

    # Shadow runtime에는 live callback 자체를 주입하지 않는다. 위 spy는 production
    # callback이 실수로 참조되지 않았음을 최종 assertion으로 고정한다.
    assert live_sender is not live_notifier
    facade = LangGraphV4RolloutFacade(
        architecture="langgraph_v4",
        mode="shadow",
        shadow_no_send=True,
        budget=_budget(),
        checkpoint_path=tmp_path / "checkpoint.sqlite3",
        daemon_db_path=tmp_path / "daemon.db",
        conversations_db_path=tmp_path / "conversation.db",
    )
    delivery = facade.shadow_delivery_runtime(InMemoryDeliveryJournal())
    persistence = PersistenceRuntime(
        journal=InMemoryPersistenceJournal(),
        writer=live_writer,
    )
    intent = DeliveryIntentV1(
        delivery_id="shadow-delivery-1",
        request_id="shadow-request-1",
        artifact_id="shadow-artifact-1",
        artifact_hash="artifact-hash",
        channel="telegram",
        destination_ref="isolated-shadow-destination",
        status=DeliveryStatus.READY,
        max_attempts=1,
    )

    receipt = await delivery.deliver(intent, "shadow content")
    persisted = await persistence.persist_delivered(
        session_key="shadow-session",
        request_id=intent.request_id,
        artifact_hash=intent.artifact_hash,
        content="shadow content",
        delivery_receipt=receipt,
    )

    assert receipt.status is DeliveryStatus.SHADOWED
    assert persisted is None
    assert calls == {"telegram": 0, "conversation": 0, "notifier": 0}
    assert not (tmp_path / "conversation.db").exists()

    with pytest.raises(ShadowNoSendConfigurationError, match="shadow_no_send"):
        LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="shadow",
            shadow_no_send=False,
            budget=_budget(),
            checkpoint_path=tmp_path / "other-checkpoint.sqlite3",
        )


def test_shadow_telemetry_preserves_contract_identity_and_sets_no_rollback() -> None:
    registry = _registry()
    invocation = _invocation(registry)
    entry = registry.asset(invocation.asset_ref)
    assert entry is not None
    result = NormalizedAssetResultV1(
        invocation_id=invocation.invocation_id,
        output_contract=invocation.output_contract,
        status=AssetResultStatus.RESOLVED,
        payload={"fixture_result": "ok"},
        payload_hash=hashlib.sha256(b"result").hexdigest(),
    )
    shadow = ShadowRunTelemetryV1.from_contract_run(
        run_id="shadow-run-1",
        request_id="shadow-request-1",
        checkpoint_thread_id="shadow:request-1",
        plan_id="shadow-plan-1",
        plan_revision=1,
        catalog_fingerprint=registry.fingerprint,
        entry=entry,
        invocation=invocation,
        selected_route="recipe",
        invocation_status=InvocationStatus.SUCCEEDED,
        result=result,
        effect_status=EffectStatus.NONE,
        terminal_outcome=TerminalOutcome.COMPLETED,
        delivery_status=DeliveryStatus.SHADOWED,
        budget_usage=_budget(),
        model_call_attribution={"planner": 1, "composer": 1, "legacy": 0},
    )
    comparison = compare_shadow_run(
        LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=1,
        ),
        shadow,
        side_effect_counts=ShadowSideEffectCountsV1(),
    )

    assert shadow.contract_owner_ref == invocation.asset_ref
    assert shadow.input_schema_hash == invocation.input_contract.schema_hash
    assert shadow.output_schema_hash == invocation.output_contract.schema_hash
    assert shadow.payload_hash == invocation.payload_hash
    assert shadow.binding_ref == entry.snapshot.declared_binding
    assert comparison.route_matches is True
    assert comparison.rollback_required is False
    assert "original_text" not in comparison.as_dict()
    assert "payload" not in comparison.as_dict()


def test_canary_gate_allows_read_only_and_rolls_back_on_drift() -> None:
    registry = _registry()
    invocation = _invocation(registry)
    entry = registry.asset(invocation.asset_ref)
    assert entry is not None
    result = NormalizedAssetResultV1(
        invocation_id=invocation.invocation_id,
        output_contract=invocation.output_contract,
        status=AssetResultStatus.RESOLVED,
        payload={"fixture_result": "ok"},
        payload_hash=hashlib.sha256(b"result").hexdigest(),
    )
    shadow = ShadowRunTelemetryV1.from_contract_run(
        run_id="shadow-run-2",
        request_id="shadow-request-2",
        checkpoint_thread_id="shadow:request-2",
        plan_id="shadow-plan-2",
        plan_revision=1,
        catalog_fingerprint=registry.fingerprint,
        entry=entry,
        invocation=invocation,
        selected_route="recipe",
        invocation_status=InvocationStatus.SUCCEEDED,
        result=result,
        effect_status=EffectStatus.NONE,
        terminal_outcome=TerminalOutcome.COMPLETED,
        delivery_status=DeliveryStatus.SHADOWED,
        budget_usage=_budget(),
        model_call_attribution={"planner": 1, "composer": 1, "legacy": 0},
    )
    comparison = compare_shadow_run(
        LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=1,
        ),
        shadow,
        side_effect_counts=ShadowSideEffectCountsV1(),
    )

    allowed = evaluate_read_only_canary(comparison, [entry.snapshot])
    drifted = compare_shadow_run(
        LegacyRunTelemetryV1(
            selected_route="react",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=1,
        ),
        shadow,
        side_effect_counts=ShadowSideEffectCountsV1(notifier=1),
    )
    rejected = evaluate_read_only_canary(drifted, [entry.snapshot])

    assert allowed.eligible is True
    assert allowed.rollback_required is False
    assert rejected.eligible is False
    assert rejected.rollback_required is True
    assert set(rejected.reasons) == {"route_mismatch", "external_side_effect"}


def test_shadow_budget_rejects_unbounded_axes_and_records_stop_condition() -> None:
    with pytest.raises(ValueError, match="all shadow budget limits must be finite"):
        ShadowBudgetUsageV1(
            max_graph_steps=40,
            max_asset_calls=12,
            max_llm_calls=8,
            max_tokens=16000,
            max_seconds=float("inf"),
            max_parallel_invocations=3,
            graph_steps=1,
            asset_calls=0,
            llm_calls=1,
            tokens=100,
            elapsed_seconds=1,
            parallel_peak=1,
            stop_condition="completed",
        )

    budget = _budget(stop_condition="deadline")
    assert budget.stop_condition == "deadline"
    assert budget.exhausted is True


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("invocation_status", InvocationStatus.FAILED_TERMINAL, "invocation_not_succeeded"),
        ("asset_result_status", AssetResultStatus.FAILED, "asset_result_not_resolved"),
        ("effect_status", EffectStatus.UNKNOWN, "effect_not_safe"),
        ("terminal_outcome", TerminalOutcome.FAILED, "terminal_outcome_mismatch"),
    ],
)
def test_canary_gate_fails_closed_for_each_unsafe_status_axis(
    field, value, reason
) -> None:
    registry = _registry()
    invocation = _invocation(registry)
    entry = registry.asset(invocation.asset_ref)
    assert entry is not None
    result = NormalizedAssetResultV1(
        invocation_id=invocation.invocation_id,
        output_contract=invocation.output_contract,
        status=AssetResultStatus.RESOLVED,
        payload={"fixture_result": "ok"},
        payload_hash=hashlib.sha256(b"result").hexdigest(),
    )
    values = {
        "invocation_status": InvocationStatus.SUCCEEDED,
        "asset_result_status": AssetResultStatus.RESOLVED,
        "effect_status": EffectStatus.NONE,
        "terminal_outcome": TerminalOutcome.COMPLETED,
    }
    values[field] = value
    if field == "asset_result_status":
        result = result.model_copy(update={"status": value})
    shadow = ShadowRunTelemetryV1.from_contract_run(
        run_id="unsafe-run",
        request_id="unsafe-request",
        checkpoint_thread_id="shadow:unsafe",
        plan_id="unsafe-plan",
        plan_revision=1,
        catalog_fingerprint=registry.fingerprint,
        entry=entry,
        invocation=invocation,
        selected_route="recipe",
        invocation_status=values["invocation_status"],
        result=result,
        effect_status=values["effect_status"],
        terminal_outcome=values["terminal_outcome"],
        delivery_status=DeliveryStatus.SHADOWED,
        budget_usage=_budget(),
        model_call_attribution={"planner": 1},
    )
    comparison = compare_shadow_run(
        LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=1,
        ),
        shadow,
        side_effect_counts=ShadowSideEffectCountsV1(),
    )
    decision = evaluate_read_only_canary(comparison, [entry.snapshot])

    assert comparison.rollback_required is True
    assert decision.eligible is False
    assert reason in decision.reasons


def test_canary_gate_rejects_budget_and_stop_condition_cross_product() -> None:
    registry = _registry()
    invocation = _invocation(registry)
    entry = registry.asset(invocation.asset_ref)
    assert entry is not None
    result = NormalizedAssetResultV1(
        invocation_id=invocation.invocation_id,
        output_contract=invocation.output_contract,
        status=AssetResultStatus.RESOLVED,
        payload={"fixture_result": "ok"},
        payload_hash=hashlib.sha256(b"result").hexdigest(),
    )
    exhausted = ShadowBudgetUsageV1(
        max_graph_steps=1,
        max_asset_calls=1,
        max_llm_calls=1,
        max_tokens=1,
        max_seconds=1,
        max_parallel_invocations=1,
        graph_steps=2,
        asset_calls=2,
        llm_calls=2,
        tokens=2,
        elapsed_seconds=2,
        parallel_peak=2,
        stop_condition="deadline",
    )
    shadow = ShadowRunTelemetryV1.from_contract_run(
        run_id="budget-run",
        request_id="budget-request",
        checkpoint_thread_id="shadow:budget",
        plan_id="budget-plan",
        plan_revision=1,
        catalog_fingerprint=registry.fingerprint,
        entry=entry,
        invocation=invocation,
        selected_route="recipe",
        invocation_status=InvocationStatus.FAILED_TERMINAL,
        result=result.model_copy(update={"status": AssetResultStatus.FAILED}),
        effect_status=EffectStatus.UNKNOWN,
        terminal_outcome=TerminalOutcome.FAILED,
        delivery_status=DeliveryStatus.SHADOWED,
        budget_usage=exhausted,
        model_call_attribution={"planner": 1},
    )
    comparison = compare_shadow_run(
        LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=1,
        ),
        shadow,
        side_effect_counts=ShadowSideEffectCountsV1(),
    )

    assert comparison.rollback_required is True
    assert {
        "invocation_not_succeeded",
        "asset_result_not_resolved",
        "effect_not_safe",
        "shadow_not_completed",
        "stop_condition_not_completed",
        "budget_exhausted",
    }.issubset(comparison.rollback_reasons)


@pytest.mark.asyncio
async def test_connected_shadow_deadline_cancels_slow_executor_before_completion(
    tmp_path,
) -> None:
    calls = {"started": 0, "completed": 0}

    async def slow_executor(_definition, _bound_steps):
        calls["started"] += 1
        await asyncio.sleep(0.2)
        calls["completed"] += 1
        return {"fixture_result": "too-late"}

    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(max_seconds=0.01),
        recipe_executor=slow_executor,
    )
    started = time.perf_counter()
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=0,
        ),
        request_id="deadline-stop",
        session_key="budget-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    assert time.perf_counter() - started < 0.15
    assert calls == {"started": 1, "completed": 0}
    assert result.telemetry.budget_usage.stop_condition == "deadline"
    assert result.telemetry.terminal_outcome is TerminalOutcome.TIMED_OUT
    assert result.telemetry.invocation_status is InvocationStatus.TIMED_OUT
    assert result.comparison.rollback_required is True
    assert result.canary.eligible is False


@pytest.mark.parametrize(
    ("budget_change", "planner_calls", "planner_tokens", "expected_asset_calls"),
    [
        ({"max_graph_steps": 1}, 0, 0, 0),
        ({"max_asset_calls": 1}, 0, 0, 1),
        ({"max_llm_calls": 1}, 1, 0, 0),
        ({"max_tokens": 1}, 0, 1, 0),
    ],
)
@pytest.mark.asyncio
async def test_connected_shadow_reserves_each_lifetime_budget_before_next_work(
    tmp_path,
    budget_change,
    planner_calls,
    planner_tokens,
    expected_asset_calls,
) -> None:
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "bounded"}

    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(**budget_change),
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=planner_calls,
            tokens=planner_tokens,
        ),
        request_id=f"axis-{next(iter(budget_change))}",
        session_key="budget-session",
        planner_model_calls=planner_calls,
        planner_tokens=planner_tokens,
    )

    usage = result.telemetry.budget_usage
    assert calls == expected_asset_calls
    assert usage.asset_calls == expected_asset_calls
    assert result.execution.dispatch_trace.executed == expected_asset_calls
    assert usage.stop_condition == "budget_exhausted"
    assert usage.exhausted is True
    assert result.telemetry.terminal_outcome is TerminalOutcome.BLOCKED
    assert result.comparison.rollback_required is True
    assert result.canary.eligible is False


@pytest.mark.asyncio
async def test_connected_shadow_max_parallel_one_completes_sequential_graph(
    tmp_path,
) -> None:
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "sequential"}

    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(max_parallel_invocations=1),
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=0,
        ),
        request_id="parallel-one-sequential",
        session_key="budget-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    usage = result.telemetry.budget_usage
    assert calls == 1
    assert usage.asset_calls == 1
    assert usage.parallel_peak == 1
    assert usage.stop_condition == "completed"
    assert result.telemetry.terminal_outcome is TerminalOutcome.COMPLETED


@pytest.mark.asyncio
async def test_connected_shadow_reserves_llm_call_and_token_cap_before_provider(
    tmp_path,
) -> None:
    provider_calls = []

    class Provider:
        async def send(self, *_args, max_tokens=None, **_kwargs):
            provider_calls.append(max_tokens)
            return LLMResponse(
                text="bounded",
                usage={"input_tokens": 10, "output_tokens": 2},
            )

    router = LLMRouter(
        backends={
            "fixture": LLMBackend(
                name="fixture",
                backend_type=BackendType.API,
                model="fixture-model",
            )
        },
        providers={"fixture": Provider()},
        default_backend="fixture",
    )

    async def executor(_definition, _bound_steps):
        await router.send(LLMRequest(user_message="one", max_tokens=100))
        # max_llm_calls=1이므로 두 번째 provider 호출은 reserve gate에서 차단된다.
        await router.send(LLMRequest(user_message="two", max_tokens=100))
        return {"fixture_result": "unreachable"}

    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(max_llm_calls=1, max_tokens=4),
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=0,
        ),
        request_id="llm-reserve",
        session_key="budget-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    assert provider_calls == [4]
    assert result.telemetry.budget_usage.llm_calls == 1
    assert result.telemetry.budget_usage.tokens == 2
    assert result.telemetry.budget_usage.stop_condition == "budget_exhausted"
    assert result.comparison.rollback_required is True


@pytest.mark.asyncio
async def test_connected_production_boundary_detects_primary_shadow_route_mismatch(
    tmp_path,
) -> None:
    async def executor(_definition, _bound_steps):
        return {"fixture_result": "bounded"}

    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(),
        recipe_executor=executor,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        # Primary execution callback이 실제로 react를 수행했다는 독립 evidence다.
        # Shadow plan projection은 recipe이므로 production comparison이 drift를 잡아야 한다.
        legacy=LegacyRunTelemetryV1(
            selected_route="react",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=2,
            tokens=20,
        ),
        request_id="connected-route-mismatch",
        session_key="budget-session",
        planner_model_calls=2,
        planner_tokens=20,
    )

    assert result.telemetry.selected_route == "recipe"
    assert result.comparison.route_matches is False
    assert "route_mismatch" in result.comparison.rollback_reasons
    assert result.canary.eligible is False


@pytest.mark.asyncio
async def test_connected_shadow_runs_graph_and_measures_production_call_points(
    tmp_path,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    intent = DeliveryIntentV1(
        delivery_id="reachable-delivery",
        request_id="reachable-request",
        artifact_id="reachable-artifact",
        artifact_hash="reachable-hash",
        channel="telegram",
        destination_ref="reachable",
        status=DeliveryStatus.READY,
        max_attempts=1,
    )

    async def sender(_destination, _content):
        return SenderReceipt(external_message_id="reachable-message")

    # 세 counter가 literal이 아니라 실제 production adapter method 진입에서
    # 증가하는지 setup에서 먼저 입증한 뒤 shadow run delta를 새로 캡처한다.
    with capture_shadow_side_effects() as reachable:
        await TelegramDeliveryAdapter(sender).send(intent, "telegram")
        await CronDeliveryAdapter(sender).send(intent, "cron")
        ConversationStorePersistenceAdapter(store)(
            "setup-session",
            "setup-persistence",
            hashlib.sha256(b"setup").hexdigest(),
            "setup",
        )
    assert (
        reachable.telegram_send,
        reachable.conversation_write,
        reachable.notifier,
    ) == (1, 1, 1)

    async def recipe_executor(_definition, _bound_steps):
        return {"fixture_result": "ok"}

    async def skill_executor(_definition, _argv):
        return {"operation_result": "ok"}

    budget = _budget()
    facade = LangGraphV4RolloutFacade(
        architecture="langgraph_v4",
        mode="shadow",
        shadow_no_send=True,
        budget=budget,
        checkpoint_path=tmp_path / "shadow-checkpoint.sqlite3",
        daemon_db_path=tmp_path / "daemon.db",
        conversations_db_path=tmp_path / "conversation.db",
    )
    runner = ConnectedShadowTurnRunner(
        facade=facade,
        definitions=_definitions(),
        conversation_store=store,
        recipe_executor=recipe_executor,
        skill_executor=skill_executor,
    )
    cases = (
        ("recipe", "contract-fixture-workflow", ExecutionMode.DIRECT_ANSWER, "recipe"),
        ("skill", "contract-fixture-step", ExecutionMode.ANSWER_WITH_EVIDENCE, "react"),
        (
            "skill",
            "contract-fixture-step",
            ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            "deep_research",
        ),
    )
    results = []
    for index, (asset_type, name, mode, route) in enumerate(cases):
        results.append(
            await runner.run(
                plan=_plan(asset_type, name, mode),
                legacy=LegacyRunTelemetryV1(
                    selected_route=route,
                    terminal_outcome=TerminalOutcome.COMPLETED,
                    model_calls=1,
                ),
                request_id=f"connected-{index}",
                session_key="connected-session",
                planner_model_calls=1,
                planner_tokens=10,
            )
        )

    assert {item.telemetry.selected_route for item in results} == {
        "recipe",
        "react",
        "deep_research",
    }
    assert all(item.telemetry.invocation_status is InvocationStatus.SUCCEEDED for item in results)
    assert all(item.telemetry.asset_result_status is AssetResultStatus.RESOLVED for item in results)
    assert all(item.telemetry.delivery_status is DeliveryStatus.SHADOWED for item in results)
    assert all(item.side_effect_counts.total == 0 for item in results)
    assert all(item.comparison.rollback_required is False for item in results)
    assert all(item.canary.eligible is True for item in results)
    assert len(
        {
            (item.telemetry.input_contract_ref, item.telemetry.output_contract_ref)
            for item in results
        }
    ) >= 2
    # setup writer 한 건 외에는 shadow가 ConversationStore에 아무것도 추가하지 않는다.
    assert [message.content for message in store.get_recent()] == ["setup"]


@pytest.mark.asyncio
async def test_production_orchestrator_consumes_v4_config_and_emits_rollout(
    tmp_path,
) -> None:
    definitions = _definitions()
    recipes = tuple(item for item in definitions if item.contract_asset_type == "recipe")
    skills = tuple(item for item in definitions if item.contract_asset_type == "skill")
    catalog = build_planner_catalog(skills=skills, recipes=recipes, native_specs=())
    gate = PlanGate().evaluate(
        replace(
            _plan(
                "recipe",
                "contract-fixture-workflow",
                ExecutionMode.DIRECT_ANSWER,
            ),
            catalog_fingerprint=catalog.fingerprint,
        ),
        candidates=ContextCandidateSet((), 0, False),
        catalog=catalog,
    )
    assert gate.status is GateStatus.PASS
    assert gate.effective_plan is not None

    class StructuredLogger:
        def __init__(self):
            self.events = []

        def log(self, **event):
            self.events.append(event)

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._store = ConversationStore(tmp_path / "production-conversation.db")
    orchestrator._structured_logger = StructuredLogger()
    orchestrator._unified_turn_planner_config = {
        "architecture": "langgraph_v4",
        "telemetry": {"enabled": True},
        "langgraph_v4": {
            "shadow_no_send": True,
            "budget": {
                "max_graph_steps": 40,
                "max_asset_calls": 12,
                "max_llm_calls": 8,
                "max_tokens": 16000,
                "max_seconds": 180,
                "max_parallel_invocations": 3,
            },
            "checkpoint": {"path": str(tmp_path / "production-shadow.sqlite3")},
            "telemetry_fields": (
                "run_id",
                "request_id",
                "selected_route",
                "invocation_status",
                "asset_result_status",
                "effect_status",
                "delivery_status",
                "budget_usage",
                "stop_condition",
            ),
        },
    }

    async def execute_recipe(self, _name, _variables, *, on_progress=None):
        return {"fixture_result": "production-connected"}

    async def execute_skill(self, _name, _arguments):
        return {"operation_result": "production-connected"}

    orchestrator._execute_exact_recipe_asset = MethodType(
        execute_recipe, orchestrator
    )
    orchestrator._execute_skill = MethodType(execute_skill, orchestrator)
    await orchestrator._run_langgraph_v4_connected_shadow(
        plan=gate.effective_plan,
        legacy=LegacyRunTelemetryV1(
            selected_route="recipe",
            terminal_outcome=TerminalOutcome.COMPLETED,
            model_calls=1,
        ),
        request_id="production-connected-request",
        session_key="production-connected-session",
        skills=skills,
        recipes=recipes,
    )

    assert orchestrator._store.get_recent() == []
    assert len(orchestrator._structured_logger.events) == 1
    event = orchestrator._structured_logger.events[0]
    assert event["action_type"] == "langgraph_v4_shadow_rollout"
    assert event["status"] == "success"
    assert event["selected_route"] == "recipe"
    assert event["side_effect_counts"] == {
        "telegram_send": 0,
        "conversation_write": 0,
        "notifier": 0,
    }
    assert event["rollback_required"] is False
    assert event["canary_eligible"] is True
