"""LangGraph V4 shadow/no-send와 read-only canary gate 회귀."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from scripts.install_naver_sports_skill import install as install_naver_sports_skill
from scripts.install_sports_live_recipe import install as install_sports_live_recipe
from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.orchestrator import (
    AgentOrchestrator,
    _allow_v4_legacy_fallback,
    _is_direct_without_asset,
    _v4_connected_contract_eligible,
)
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
from simpleclaw.agent.turn_state import TurnExecutionState
from simpleclaw.graph_runtime.adapters.base import AdapterResponse
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
from simpleclaw.graph_runtime.idempotency import (
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
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
    ConnectedExecutionError,
    ConnectedShadowTurnRunner,
    DurableDispatchProvenanceV1,
    _DurableInvocationClaims,
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
from scripts.dev.validate_langgraph_v4_no_send import (
    definitions as _connected_validation_definitions,
)
from simpleclaw.llm.models import BackendType, LLMBackend, LLMRequest, LLMResponse
from simpleclaw.llm.router import LLMRouter
from simpleclaw.memory import ConversationStore
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


def _production_sports_definitions(tmp_path: Path):
    recipes_dir = tmp_path / "production-recipes"
    global_skills = tmp_path / "production-skills"
    install_sports_live_recipe(recipes_dir)
    install_naver_sports_skill(global_skills)
    recipe = next(
        item
        for item in discover_recipes(recipes_dir)
        if item.name == "sports-live"
    )
    skill = next(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"),
            global_skills,
        )
        if item.name == "naver-sports-skill"
    )
    return recipe, skill


def _kbo_incident_plan(catalog_fingerprint: str) -> UnifiedTurnPlan:
    prompt = "Kbo 순위 상위 3팀 알려줘"
    return UnifiedTurnPlan(
        original_text=prompt,
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question=prompt,
        ),
        clarification=ClarificationPlan(required=False),
        domains=("sports",),
        intents=("standings",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="sports",
            entities=(),
            search_query="",
            intents=("standings",),
            freshness_required=True,
        ),
        execution=ExecutionPlan(mode=ExecutionMode.DIRECT_ANSWER),
        capability=CapabilityPlan(coverage=CapabilityCoverage.NO_MATCH),
        confidence=1.0,
        decision_summary="incident fixture",
        catalog_fingerprint=catalog_fingerprint,
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
    definition = next(
        item
        for item in _definitions()
        if (item.contract_asset_type, item.name) == (asset_type, name)
    )
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
        approved_asset_fingerprint=definition.definition_fingerprint,
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
@pytest.mark.offline
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
    assert result.execution.final_content == "primary-source"
    assert result.execution.provenance.startswith(
        "langgraph_v4:recipe:contract-fixture-workflow:"
    )
    assert result.execution.dispatch_trace.exactly_once is True
    assert result.execution.side_effect_counts.total == 0
    assert result.execution.rollback_required is False
    assert store.get_recent() == []


@pytest.mark.asyncio
@pytest.mark.offline
async def test_kbo_asset_zero_plan_repairs_and_completes_three_no_send_runs(
    tmp_path,
) -> None:
    recipe, skill = _production_sports_definitions(tmp_path)
    catalog = build_planner_catalog(
        skills=(skill,),
        recipes=(recipe,),
        native_specs=(),
    )
    original = _kbo_incident_plan(catalog.fingerprint)
    gate = PlanGate().evaluate(
        original,
        candidates=ContextCandidateSet((), 0, False),
        catalog=catalog,
    )
    assert gate.status is GateStatus.PASS
    assert gate.effective_plan is not None
    assert original.capability.primary_asset is None
    assert gate.effective_plan.capability.primary_asset == AssetRef(
        "recipe", "sports-live"
    )
    assert _v4_connected_contract_eligible(gate.effective_plan, catalog) is True

    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {
            "schema": "asset_result.v1",
            "status": "resolved",
            "data": {"standings": ["team-1", "team-2", "team-3"]},
        }

    store = ConversationStore(tmp_path / "kbo-conversation.db")
    runner = ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget_with(),
            checkpoint_path=tmp_path / "kbo-checkpoint.sqlite3",
            daemon_db_path=tmp_path / "daemon.db",
            conversations_db_path=tmp_path / "kbo-conversation.db",
        ),
        definitions=(recipe, skill),
        conversation_store=store,
        recipe_executor=executor,
    )

    for index in range(3):
        result = await runner.run(
            plan=gate.effective_plan,
            legacy=None,
            request_id=f"kbo-incident-{index}",
            session_key="isolated-kbo-session",
            planner_model_calls=1,
            planner_tokens=100,
        )
        assert result.execution.final_content is not None
        assert result.execution.dispatch_trace.exactly_once is True
        assert result.execution.rollback_required is False
        assert result.execution.side_effect_counts.total == 0

    assert calls == 3
    assert store.get_recent() == []


@pytest.mark.asyncio
@pytest.mark.offline
async def test_kbo_live_stale_contract_fails_before_connected_dispatch(
    tmp_path,
    monkeypatch,
) -> None:
    recipe, _skill = _production_sports_definitions(tmp_path)
    stale_recipe = replace(
        recipe,
        input_contract=None,
        output_contract=None,
        step_bindings=(),
    )

    async def fake_planner(_text, *, catalog, **_kwargs):
        return _kbo_incident_plan(catalog.fingerprint)

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
        "langgraph_v4": {"budget_valid": True},
    }
    orchestrator._router = object()
    orchestrator._cron_scheduler = None
    orchestrator._browser_handoff_config = {"enabled": False}
    orchestrator._recipes = (stale_recipe,)
    orchestrator._store = ConversationStore(tmp_path / "stale-contract.db")
    orchestrator._structured_logger = None
    orchestrator._exposable_skills = MethodType(lambda self: [], orchestrator)
    connected_calls = 0
    rollout_events: list[dict[str, object]] = []

    async def connected_must_not_run(self, **_kwargs):
        nonlocal connected_calls
        connected_calls += 1
        raise AssertionError("incomplete connected snapshot reached dispatch")

    def record_rollout(self, **event):
        rollout_events.append(event)

    orchestrator._execute_langgraph_v4_connected = MethodType(
        connected_must_not_run,
        orchestrator,
    )
    orchestrator._record_unified_rollout_path = MethodType(
        record_rollout,
        orchestrator,
    )
    turn = TurnExecutionState.create(
        session_key="stale-kbo-session",
        original_text="Kbo 순위 상위 3팀 알려줘",
        turn_id="stale-kbo-request",
    )

    result = await orchestrator._run_unified_turn_planner_primary(
        turn.original_text,
        recent_rows=[],
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        operator_tools=False,
        turn=turn,
    )

    assert result.success is False
    assert connected_calls == 0
    assert turn.phase.value == "rejected"
    assert rollout_events[-1]["reason"] == "gate_repair"


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_primary_reentry_reuses_durable_terminal_without_dispatch(
    tmp_path,
) -> None:
    calls = 0
    checkpoint = tmp_path / "durable-checkpoint.sqlite3"

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "durable-result"}

    def runner() -> ConnectedShadowTurnRunner:
        return ConnectedShadowTurnRunner(
            facade=LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode="primary",
                shadow_no_send=True,
                budget=_budget_with(),
                checkpoint_path=checkpoint,
            ),
            definitions=_definitions(),
            conversation_store=ConversationStore(tmp_path / "durable-conversation.db"),
            recipe_executor=executor,
        )

    kwargs = {
        "plan": _plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        "legacy": None,
        "request_id": "durable-request",
        "session_key": "durable-session",
        "planner_model_calls": 0,
        "planner_tokens": 0,
    }
    first = await runner().run(**kwargs)
    resumed = await runner().run(**kwargs)

    assert calls == 1
    assert first.execution.final_content == "durable-result"
    assert resumed.execution.final_content == "durable-result"
    assert resumed.execution.dispatch_trace.exactly_once is True
    assert resumed.execution.rollback_required is False


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_primary_concurrent_owner_waits_and_reuses_terminal(
    tmp_path,
) -> None:
    calls = 0
    checkpoint = tmp_path / "concurrent-checkpoint.sqlite3"
    started = asyncio.Event()
    release = asyncio.Event()

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"fixture_result": "concurrent-result"}

    def runner() -> ConnectedShadowTurnRunner:
        return ConnectedShadowTurnRunner(
            facade=LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode="primary",
                shadow_no_send=True,
                budget=_budget_with(),
                checkpoint_path=checkpoint,
            ),
            definitions=_definitions(),
            conversation_store=ConversationStore(tmp_path / "concurrent.db"),
            recipe_executor=executor,
        )

    kwargs = {
        "plan": _plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        "legacy": None,
        "request_id": "concurrent-request",
        "session_key": "concurrent-session",
        "planner_model_calls": 0,
        "planner_tokens": 0,
    }
    owner = asyncio.create_task(runner().run(**kwargs))
    await started.wait()
    waiter = asyncio.create_task(runner().run(**kwargs))
    await asyncio.sleep(0.03)
    assert calls == 1
    release.set()
    first, second = await asyncio.gather(owner, waiter)

    assert calls == 1
    assert first.execution.final_content == "concurrent-result"
    assert second.execution.final_content == "concurrent-result"
    assert second.execution.dispatch_trace.exactly_once is True


def _request_drift_plan(axis: str) -> UnifiedTurnPlan:
    if axis == "asset":
        return _plan(
            "skill",
            "contract-fixture-step",
            ExecutionMode.ANSWER_WITH_EVIDENCE,
        )
    plan = _plan(
        "recipe",
        "contract-fixture-workflow",
        ExecutionMode.DIRECT_ANSWER,
    )
    return replace(
        plan,
        context=replace(
            plan.context,
            standalone_question="connected-shadow-drifted-value",
        ),
    )


@pytest.mark.asyncio
@pytest.mark.offline
@pytest.mark.parametrize("axis", ("asset", "payload"))
async def test_connected_primary_request_claim_rejects_sequential_drift(
    tmp_path,
    axis: str,
) -> None:
    calls = {"recipe": 0, "skill": 0}
    checkpoint = tmp_path / f"sequential-{axis}-claim.sqlite3"

    async def recipe_executor(_definition, _bound_steps):
        calls["recipe"] += 1
        return {"fixture_result": "immutable-result"}

    async def skill_executor(_definition, _argv):
        calls["skill"] += 1
        return {"operation_result": "must-not-run"}

    def runner() -> ConnectedShadowTurnRunner:
        return ConnectedShadowTurnRunner(
            facade=LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode="primary",
                shadow_no_send=True,
                budget=_budget_with(),
                checkpoint_path=checkpoint,
            ),
            definitions=_definitions(),
            conversation_store=ConversationStore(
                tmp_path / f"sequential-{axis}-conversation.db"
            ),
            recipe_executor=recipe_executor,
            skill_executor=skill_executor,
        )

    first = await runner().run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=None,
        request_id="same-sequential-request",
        session_key="same-sequential-session",
        planner_model_calls=0,
        planner_tokens=0,
    )
    drifted = await runner().run(
        plan=_request_drift_plan(axis),
        legacy=None,
        request_id="same-sequential-request",
        session_key="same-sequential-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    assert calls == {"recipe": 1, "skill": 0}
    assert first.execution.final_content == "immutable-result"
    assert drifted.execution.final_artifact is None
    assert drifted.execution.rollback_required is True
    error_code = (
        "request_identity_mismatch"
        if axis == "asset"
        else "invocation_identity_mismatch"
    )
    assert error_code in drifted.execution.rollback_reasons
    database = checkpoint.with_name(f"{checkpoint.name}.invocations.sqlite3")
    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM graph_request_claims"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM graph_invocation_claims"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.offline
@pytest.mark.parametrize("axis", ("asset", "payload"))
async def test_connected_primary_request_claim_rejects_concurrent_drift(
    tmp_path,
    axis: str,
) -> None:
    calls = {"recipe": 0, "skill": 0}
    checkpoint = tmp_path / f"concurrent-{axis}-claim.sqlite3"
    started = asyncio.Event()
    release = asyncio.Event()

    async def recipe_executor(_definition, _bound_steps):
        calls["recipe"] += 1
        started.set()
        await release.wait()
        return {"fixture_result": "immutable-result"}

    async def skill_executor(_definition, _argv):
        calls["skill"] += 1
        return {"operation_result": "must-not-run"}

    def runner() -> ConnectedShadowTurnRunner:
        return ConnectedShadowTurnRunner(
            facade=LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode="primary",
                shadow_no_send=True,
                budget=_budget_with(),
                checkpoint_path=checkpoint,
            ),
            definitions=_definitions(),
            conversation_store=ConversationStore(
                tmp_path / f"concurrent-{axis}-conversation.db"
            ),
            recipe_executor=recipe_executor,
            skill_executor=skill_executor,
        )

    owner = asyncio.create_task(
        runner().run(
            plan=_plan(
                "recipe",
                "contract-fixture-workflow",
                ExecutionMode.DIRECT_ANSWER,
            ),
            legacy=None,
            request_id="same-concurrent-request",
            session_key="same-concurrent-session",
            planner_model_calls=0,
            planner_tokens=0,
        )
    )
    await started.wait()
    contender = asyncio.create_task(
        runner().run(
            plan=_request_drift_plan(axis),
            legacy=None,
            request_id="same-concurrent-request",
            session_key="same-concurrent-session",
            planner_model_calls=0,
            planner_tokens=0,
        )
    )
    await asyncio.sleep(0.03)
    assert calls == {"recipe": 1, "skill": 0}
    release.set()
    first, drifted = await asyncio.gather(owner, contender)

    assert calls == {"recipe": 1, "skill": 0}
    assert first.execution.final_content == "immutable-result"
    assert drifted.execution.final_artifact is None
    assert drifted.execution.rollback_required is True
    error_code = (
        "request_identity_mismatch"
        if axis == "asset"
        else "invocation_identity_mismatch"
    )
    assert error_code in drifted.execution.rollback_reasons
    database = checkpoint.with_name(f"{checkpoint.name}.invocations.sqlite3")
    with sqlite3.connect(database) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM graph_request_claims"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM graph_invocation_claims"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_terminal_resume_uses_composition_guard_and_safe_renderer(
    tmp_path,
) -> None:
    calls = 0
    checkpoint = tmp_path / "guard-checkpoint.sqlite3"

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": '{"unsafe":true}'}

    def runner() -> ConnectedShadowTurnRunner:
        return ConnectedShadowTurnRunner(
            facade=LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode="primary",
                shadow_no_send=True,
                budget=_budget_with(),
                checkpoint_path=checkpoint,
            ),
            definitions=_definitions(),
            conversation_store=ConversationStore(tmp_path / "guard.db"),
            recipe_executor=executor,
        )

    kwargs = {
        "plan": _plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        "legacy": None,
        "request_id": "guard-request",
        "session_key": "guard-session",
        "planner_model_calls": 0,
        "planner_tokens": 0,
    }
    first = await runner().run(**kwargs)
    resumed = await runner().run(**kwargs)

    expected = "요청을 처리했지만 안전한 응답을 구성하지 못했습니다."
    assert calls == 1
    assert first.execution.final_content == expected
    assert resumed.execution.final_content == expected


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_corrupt_terminal_resume_promotes_no_artifact(tmp_path) -> None:
    checkpoint = tmp_path / "corrupt-resume.sqlite3"
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "original"}

    def runner() -> ConnectedShadowTurnRunner:
        return ConnectedShadowTurnRunner(
            facade=LangGraphV4RolloutFacade(
                architecture="langgraph_v4",
                mode="primary",
                shadow_no_send=True,
                budget=_budget_with(),
                checkpoint_path=checkpoint,
            ),
            definitions=_definitions(),
            conversation_store=ConversationStore(tmp_path / "corrupt-resume.db"),
            recipe_executor=executor,
        )

    kwargs = {
        "plan": _plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        "legacy": None,
        "request_id": "corrupt-resume-request",
        "session_key": "corrupt-resume-session",
        "planner_model_calls": 0,
        "planner_tokens": 0,
    }
    first = await runner().run(**kwargs)
    assert first.execution.final_content == "original"
    database = checkpoint.with_name(f"{checkpoint.name}.invocations.sqlite3")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE graph_invocation_claims SET response_json = ?",
            ("{not-json",),
        )

    resumed = await runner().run(**kwargs)

    assert calls == 1
    assert resumed.execution.final_artifact is None
    assert resumed.execution.rollback_required is True
    assert "corrupt_terminal_response" in resumed.execution.rollback_reasons


@pytest.mark.asyncio
@pytest.mark.offline
async def test_durable_claimed_invocation_recovery_is_fenced(tmp_path) -> None:
    invocation = _invocation(_registry())
    checkpoint = tmp_path / "claim-checkpoint.sqlite3"
    first = _DurableInvocationClaims(checkpoint, lease_seconds=0.01)
    resumed = _DurableInvocationClaims(checkpoint, lease_seconds=0.01)

    assert await first.claim("claim-request", invocation) is None
    await asyncio.sleep(0.02)
    assert await resumed.claim("claim-request", invocation) is None
    with pytest.raises(_TargetDispatchInvariantError, match="claim_not_dispatchable"):
        first.mark_executed(invocation.invocation_id)


def _terminal_response(invocation: AssetInvocationV1) -> AdapterResponse:
    payload = {"fixture_result": "durable-result"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    result = NormalizedAssetResultV1(
        invocation_id=invocation.invocation_id,
        output_contract=invocation.output_contract,
        status=AssetResultStatus.RESOLVED,
        payload=payload,
        payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        effect_status=EffectStatus.NONE,
    )
    return AdapterResponse(
        invocation_id=invocation.invocation_id,
        status=AssetResultStatus.RESOLVED,
        input_payload_hash=invocation.payload_hash,
        effect_status=EffectStatus.NONE,
        result=result,
        dispatched=True,
    )


async def _seed_terminal(checkpoint: Path, invocation: AssetInvocationV1) -> None:
    owner = _DurableInvocationClaims(checkpoint)
    assert await owner.claim("claim-request", invocation) is None
    owner.mark_executed(invocation.invocation_id)
    owner.mark_terminal(invocation, _terminal_response(invocation))


@pytest.mark.asyncio
@pytest.mark.offline
async def test_durable_active_owner_waiter_reuses_terminal_without_ambiguity(
    tmp_path,
) -> None:
    invocation = _invocation(_registry())
    checkpoint = tmp_path / "active-owner.sqlite3"
    owner = _DurableInvocationClaims(checkpoint, lease_seconds=0.03)
    waiter = _DurableInvocationClaims(checkpoint, lease_seconds=0.03)
    assert await owner.claim("claim-request", invocation) is None
    owner.mark_executed(invocation.invocation_id)
    heartbeat = asyncio.create_task(owner.renew_lease(invocation.invocation_id))

    waiting = asyncio.create_task(waiter.claim("claim-request", invocation))
    await asyncio.sleep(0.08)
    assert waiting.done() is False
    owner.mark_terminal(invocation, _terminal_response(invocation))
    heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat

    reused = await asyncio.wait_for(waiting, timeout=1)
    assert reused is not None
    assert reused.receipt_reused is True
    assert waiter.provenance("claim-request").lifecycle == "terminal"


@pytest.mark.asyncio
@pytest.mark.offline
async def test_expired_executed_owner_becomes_ambiguous_and_stale_cas_fails(
    tmp_path,
) -> None:
    invocation = _invocation(_registry())
    checkpoint = tmp_path / "expired-owner.sqlite3"
    owner = _DurableInvocationClaims(checkpoint, lease_seconds=0.01)
    recovery = _DurableInvocationClaims(checkpoint, lease_seconds=0.01)
    assert await owner.claim("claim-request", invocation) is None
    owner.mark_executed(invocation.invocation_id)
    await asyncio.sleep(0.02)

    with pytest.raises(_TargetDispatchInvariantError, match="manual_recovery_required"):
        await recovery.claim("claim-request", invocation)
    with pytest.raises(_TargetDispatchInvariantError, match="claim_not_executed"):
        owner.mark_terminal(invocation, _terminal_response(invocation))
    assert recovery.provenance("claim-request").lifecycle == "ambiguous"


@pytest.mark.asyncio
@pytest.mark.offline
async def test_durable_terminal_corruption_is_rejected(tmp_path) -> None:
    invocation = _invocation(_registry())
    checkpoint = tmp_path / "corrupt-terminal.sqlite3"
    await _seed_terminal(checkpoint, invocation)
    database = checkpoint.with_name(f"{checkpoint.name}.invocations.sqlite3")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE graph_invocation_claims SET response_json = ?",
            ("{not-json",),
        )

    with pytest.raises(_TargetDispatchInvariantError, match="corrupt_terminal_response"):
        _DurableInvocationClaims(checkpoint).terminal("claim-request", invocation)


@pytest.mark.asyncio
@pytest.mark.offline
@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.__setitem__("invocation_id", "foreign"),
        lambda value: value.__setitem__("input_payload_hash", "foreign"),
        lambda value: value["result"].__setitem__("invocation_id", "foreign"),
        lambda value: value["result"]["output_contract"].__setitem__(
            "schema_hash", "foreign"
        ),
        lambda value: value["result"].__setitem__("payload_hash", "foreign"),
        lambda value: value["result"].__setitem__("status", "failed"),
        lambda value: value["result"].__setitem__("effect_status", "verified"),
        lambda value: value.__setitem__("dispatched", False),
    ),
)
async def test_durable_terminal_receipt_response_mismatch_is_rejected(
    tmp_path,
    mutation,
) -> None:
    invocation = _invocation(_registry())
    checkpoint = tmp_path / "mismatch-terminal.sqlite3"
    await _seed_terminal(checkpoint, invocation)
    database = checkpoint.with_name(f"{checkpoint.name}.invocations.sqlite3")
    with sqlite3.connect(database) as conn:
        raw = conn.execute(
            "SELECT response_json FROM graph_invocation_claims"
        ).fetchone()[0]
        value = json.loads(raw)
        mutation(value)
        conn.execute(
            "UPDATE graph_invocation_claims SET response_json = ?",
            (json.dumps(value, sort_keys=True, separators=(",", ":")),),
        )

    with pytest.raises(_TargetDispatchInvariantError):
        _DurableInvocationClaims(checkpoint).terminal("claim-request", invocation)


@pytest.mark.asyncio
@pytest.mark.offline
@pytest.mark.parametrize("axis", ("definition", "input", "output", "binding"))
async def test_durable_invocation_signature_drift_is_rejected(
    tmp_path,
    axis: str,
) -> None:
    registry = _registry()
    invocation = _invocation(registry)
    entry = registry.asset(invocation.asset_ref)
    assert entry is not None
    binding = entry.snapshot.declared_binding
    checkpoint = tmp_path / "signature-drift.sqlite3"
    owner = _DurableInvocationClaims(checkpoint)
    assert await owner.claim(
        "claim-request", invocation, binding_ref=binding
    ) is None
    owner.mark_executed(invocation.invocation_id)
    owner.mark_terminal(invocation, _terminal_response(invocation))

    drifted = invocation
    drifted_binding = binding
    if axis == "definition":
        drifted = invocation.model_copy(
            update={"definition_fingerprint": "drifted"}
        )
    elif axis == "input":
        drifted = invocation.model_copy(
            update={
                "input_contract": invocation.input_contract.model_copy(
                    update={"schema_hash": "drifted"}
                )
            }
        )
    elif axis == "output":
        drifted = invocation.model_copy(
            update={
                "output_contract": invocation.output_contract.model_copy(
                    update={"schema_hash": "drifted"}
                )
            }
        )
    else:
        assert binding is not None
        drifted_binding = binding.model_copy(update={"binding_hash": "drifted"})

    with pytest.raises(_TargetDispatchInvariantError, match="invocation_identity_mismatch"):
        _DurableInvocationClaims(checkpoint).terminal(
            "claim-request",
            drifted,
            binding_ref=drifted_binding,
        )


@pytest.mark.offline
def test_legacy_fallback_is_blocked_after_v4_target_dispatch() -> None:
    invocation = _invocation(_registry())
    receipt = LangGraphV4ExecutionReceiptV1(
        mode="primary",
        request_id="fallback-request",
        selected_route="recipe",
        final_artifact=None,
        dispatch_trace=TargetDispatchTraceV1(
            target_asset_ref=invocation.asset_ref,
            invocation_id=invocation.invocation_id,
            attempted=1,
            executed=1,
            succeeded=0,
        ),
        budget_usage=_budget(),
        side_effect_counts=ShadowSideEffectCountsV1(),
        terminal_outcome=TerminalOutcome.FAILED,
        rollback_required=True,
        rollback_reasons=("post_dispatch_failure",),
    )

    not_started = DurableDispatchProvenanceV1(
        request_id="fallback-request",
        lifecycle="not_started",
    )
    executed = DurableDispatchProvenanceV1(
        request_id="fallback-request",
        lifecycle="executed",
        invocation_id=invocation.invocation_id,
    )

    assert _allow_v4_legacy_fallback(
        {"on_failure": "legacy"}, None, not_started
    ) is True
    assert _allow_v4_legacy_fallback(
        {"on_failure": "legacy"}, None, None
    ) is False
    assert _allow_v4_legacy_fallback(
        {"on_failure": "legacy"}, receipt, executed
    ) is False


@pytest.mark.asyncio
@pytest.mark.offline
async def test_orchestrator_receipt_loss_after_dispatch_never_runs_legacy(
    tmp_path,
    monkeypatch,
    caplog,
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
        capability=replace(planned.capability, coverage=CapabilityCoverage.FULL),
    )

    async def fake_planner(_text, *, catalog, **_kwargs):
        return replace(planned, catalog_fingerprint=catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    checkpoint = tmp_path / "receipt-loss-checkpoint.sqlite3"
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
            "on_failure": "legacy",
            "checkpoint": {"path": str(checkpoint)},
        },
    }
    orchestrator._router = object()
    orchestrator._cron_scheduler = None
    orchestrator._browser_handoff_config = {"enabled": False}
    orchestrator._recipes = recipes
    orchestrator._store = ConversationStore(tmp_path / "receipt-loss.db")
    orchestrator._structured_logger = None
    orchestrator._exposable_skills = MethodType(
        lambda self: list(skills), orchestrator
    )
    legacy_calls = 0

    async def execute_then_lose_receipt(self, **kwargs):
        invocation = _invocation(_registry())
        claims = _DurableInvocationClaims(checkpoint)
        assert await claims.claim(kwargs["request_id"], invocation) is None
        claims.mark_executed(invocation.invocation_id)
        claims.mark_ambiguous(invocation.invocation_id)
        try:
            raise ValueError(
                'ASCII_PRIVATE_PROMPT_MARKER_639 '
                '{"api_key":"json-private-key","password":"json-password"} '
                'https://provider.example/v1?token=url-private-token 사용자 비공개 질문'
            )
        except ValueError as cause:
            raise RuntimeError("BEARER provider-private-token") from cause

    async def legacy_must_not_run(self, *_args, **_kwargs):
        nonlocal legacy_calls
        legacy_calls += 1
        raise AssertionError("post-dispatch legacy fallback must not run")

    orchestrator._execute_langgraph_v4_connected = MethodType(
        execute_then_lose_receipt,
        orchestrator,
    )
    orchestrator._run_tool_loop_result = MethodType(
        legacy_must_not_run,
        orchestrator,
    )
    turn = TurnExecutionState.create(
        session_key="receipt-loss-session",
        original_text=planned.original_text,
        turn_id="receipt-loss-request",
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

    assert result.success is False
    assert legacy_calls == 0
    assert turn.phase.value == "rejected"
    assert _DurableInvocationClaims(checkpoint).provenance(
        turn.turn_id
    ).lifecycle == "ambiguous"
    messages = caplog.text
    assert "request_id=receipt-loss-request" in messages
    assert "original_mode=direct_answer" in messages
    assert "effective_mode=direct_answer" in messages
    assert "original_asset=recipe:contract-fixture-workflow" in messages
    assert "effective_asset=recipe:contract-fixture-workflow" in messages
    assert "failure_phase=setup" in messages
    assert "phase=setup" in messages
    assert "code=connected_setup_failed" in messages
    assert "error_type=RuntimeError" in messages
    assert (
        "selected_asset_identity=recipe:contract-fixture-workflow" in messages
    )
    assert "catalog_fingerprint=" in messages
    assert "registry_fingerprint=" in messages
    assert "owned_input_contract_present=None" in messages
    assert "owned_output_contract_present=None" in messages
    assert "owned_binding_present=None" in messages
    assert "error_message=message_sha256=" in messages
    assert planned.original_text not in messages
    assert "ASCII_PRIVATE_PROMPT_MARKER_639" not in messages
    assert "json-private-key" not in messages
    assert "json-password" not in messages
    assert "provider-private-token" not in messages
    assert "provider.example" not in messages
    assert "사용자 비공개 질문" not in messages
    assert "Traceback" not in messages


@pytest.mark.offline
def test_execution_receipt_rejects_false_success_invariants() -> None:
    invocation = _invocation(_registry())
    with pytest.raises(ValueError, match="exactly-one dispatch"):
        LangGraphV4ExecutionReceiptV1(
            mode="primary",
            request_id="false-success",
            selected_route="recipe",
            final_artifact=None,
            dispatch_trace=TargetDispatchTraceV1(
                target_asset_ref=invocation.asset_ref,
                invocation_id=invocation.invocation_id,
                attempted=0,
                executed=0,
                succeeded=0,
            ),
            budget_usage=_budget(),
            side_effect_counts=ShadowSideEffectCountsV1(),
            terminal_outcome=TerminalOutcome.COMPLETED,
            rollback_required=False,
            rollback_reasons=(),
        )


@pytest.mark.offline
@pytest.mark.parametrize(
    "artifact_update",
    [
        {"artifact_id": "arbitrary-artifact"},
        {"request_id": "stale-request"},
        {"content": "stale content"},
        {"content_hash": "stale-content-hash"},
    ],
)
def test_execution_receipt_rejects_noncanonical_final_artifact(
    artifact_update,
) -> None:
    request_id = "receipt-request"
    content = "canonical answer"
    invocation = _invocation(_registry())
    final = FinalArtifactV1(
        artifact_id=canonical_artifact_id(request_id, content),
        request_id=request_id,
        content=content,
        outcome=TerminalOutcome.COMPLETED,
        content_hash=canonical_artifact_content_hash(content),
    ).model_copy(update=artifact_update)

    with pytest.raises(ValueError, match="(identity|content hash) mismatch"):
        LangGraphV4ExecutionReceiptV1(
            mode="primary",
            request_id=request_id,
            selected_route="recipe",
            final_artifact=final,
            dispatch_trace=TargetDispatchTraceV1(
                target_asset_ref=invocation.asset_ref,
                invocation_id=invocation.invocation_id,
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


@pytest.mark.offline
def test_v4_primary_preserves_direct_no_asset_parity() -> None:
    planned = _plan(
        "recipe",
        "contract-fixture-workflow",
        ExecutionMode.DIRECT_ANSWER,
    )
    direct = replace(
        planned,
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.NO_MATCH,
            primary_asset=None,
            supporting_assets=(),
        ),
        execution=replace(
            planned.execution,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=(),
        ),
    )

    assert _is_direct_without_asset(direct) is True


@pytest.mark.offline
def test_v4_connected_rejects_planner_asset_without_owned_contract_snapshot() -> None:
    definitions = _definitions()
    catalog = build_planner_catalog(
        skills=tuple(
            item for item in definitions if item.contract_asset_type == "skill"
        ),
        recipes=tuple(
            item for item in definitions if item.contract_asset_type == "recipe"
        ),
        native_specs=(),
    )
    plan = _plan(
        "recipe",
        "contract-fixture-workflow",
        ExecutionMode.DIRECT_ANSWER,
    )
    plan = replace(
        plan,
        capability=replace(plan.capability, coverage=CapabilityCoverage.FULL),
    )

    assert _v4_connected_contract_eligible(plan, catalog) is True

    selected = next(
        asset for asset in catalog.assets if asset.name == "contract-fixture-workflow"
    )
    stale_catalog = replace(
        catalog,
        assets=tuple(
            replace(
                asset,
                contract_owner=None,
                input_contract_ref=None,
                output_contract_ref=None,
                input_schema_hash=None,
                output_schema_hash=None,
                binding_identity=None,
                definition_fingerprint=None,
            )
            if asset is selected
            else asset
            for asset in catalog.assets
        ),
    )

    assert _v4_connected_contract_eligible(plan, stale_catalog) is False


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_registry_failure_preserves_phase_cause_and_redacts_prompt(
    tmp_path,
) -> None:
    plan = replace(
        _plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        capability=replace(
            _plan(
                "recipe",
                "contract-fixture-workflow",
                ExecutionMode.DIRECT_ANSWER,
            ).capability,
            primary_asset=AssetRef("recipe", "missing-KBO-원문"),
        ),
    )
    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(),
        recipe_executor=None,
    )

    with pytest.raises(ConnectedExecutionError) as captured:
        await runner.run(
            plan=plan,
            legacy=None,
            request_id="registry-failure-request",
            session_key="isolated-session",
            planner_model_calls=1,
            planner_tokens=10,
        )

    assert captured.value.phase == "registry_lookup"
    assert captured.value.code == "asset_not_registered_read_only"
    assert captured.value.error_type == "ValueError"
    assert captured.value.selected_asset_identity == "recipe:missing-KBO-원문"
    assert captured.value.catalog_fingerprint == plan.catalog_fingerprint
    assert captured.value.registry_fingerprint
    assert captured.value.owned_input_contract_present is False
    assert captured.value.owned_output_contract_present is False
    assert captured.value.owned_binding_present is False
    assert "KBO" not in captured.value.safe_message
    assert isinstance(captured.value.__cause__, ValueError)


@pytest.mark.parametrize("drift_axis", ("definition", "binding", "executor"))
@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_runner_blocks_catalog_registry_snapshot_drift_before_dispatch(
    tmp_path,
    drift_axis,
) -> None:
    definitions = _definitions()
    recipe = next(
        item for item in definitions if item.name == "contract-fixture-workflow"
    )
    catalog = build_planner_catalog(
        skills=tuple(
            item for item in definitions if item.contract_asset_type == "skill"
        ),
        recipes=tuple(
            item for item in definitions if item.contract_asset_type == "recipe"
        ),
        native_specs=(),
    )
    planned = replace(
        _plan("recipe", recipe.name, ExecutionMode.DIRECT_ANSWER),
        catalog_fingerprint=catalog.fingerprint,
        approved_asset_fingerprint="",
    )
    gate = PlanGate().evaluate(
        planned,
        candidates=ContextCandidateSet((), 0, False),
        catalog=catalog,
    )
    assert gate.status is GateStatus.PASS
    assert gate.effective_plan is not None
    approved = gate.effective_plan
    assert approved.approved_asset_fingerprint == recipe.definition_fingerprint

    if drift_axis == "definition":
        drifted_recipe = replace(recipe, description=f"{recipe.description} drift")
    elif drift_axis == "binding":
        drifted_recipe = replace(
            recipe,
            step_bindings=(
                replace(recipe.step_bindings[0], binding_id="fixture-step.v2"),
            ),
        )
    else:
        drifted_recipe = replace(recipe, instructions="changed executor definition")
    drifted_definitions = tuple(
        drifted_recipe if item is recipe else item for item in definitions
    )
    original_registry = build_contract_registry(definitions)
    drifted_registry = build_contract_registry(drifted_definitions)
    owner = AssetRefV1(type="recipe", name=recipe.name)
    original_entry = original_registry.asset(owner)
    drifted_entry = drifted_registry.asset(owner)
    assert original_entry is not None
    assert drifted_entry is not None
    assert original_entry.input_descriptor.ref == drifted_entry.input_descriptor.ref
    assert original_entry.output_descriptor.ref == drifted_entry.output_descriptor.ref
    assert (
        original_entry.snapshot.definition_fingerprint
        != drifted_entry.snapshot.definition_fingerprint
    )

    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "must-not-run"}

    store = ConversationStore(tmp_path / f"{drift_axis}-conversation.db")
    runner = ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget_with(),
            checkpoint_path=tmp_path / f"{drift_axis}-checkpoint.sqlite3",
        ),
        definitions=drifted_definitions,
        conversation_store=store,
        recipe_executor=executor,
    )

    with pytest.raises(ConnectedExecutionError) as captured:
        await runner.run(
            plan=approved,
            legacy=None,
            request_id=f"toctou-{drift_axis}",
            session_key="toctou-session",
            planner_model_calls=1,
            planner_tokens=10,
        )

    assert captured.value.phase == "registry_lookup"
    assert captured.value.code == "approved_asset_fingerprint_mismatch"
    assert captured.value.selected_asset_identity == f"recipe:{recipe.name}"
    assert captured.value.approved_asset_hash == recipe.definition_fingerprint
    assert captured.value.selected_asset_hash == drifted_recipe.definition_fingerprint
    assert calls == 0
    assert store.get_recent() == []
    assert _allow_v4_legacy_fallback(
        {"on_failure": "legacy"}, None, None
    ) is False


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_dispatch_valueerror_is_typed_and_sanitized(
    tmp_path,
    monkeypatch,
) -> None:
    async def fail_dispatch(_self, _invocation, **_kwargs):
        raise ValueError("provider dispatch failed token=top-secret")

    monkeypatch.setattr(
        "simpleclaw.graph_runtime.adapters.recipe.GenericRecipeAdapter.dispatch",
        fail_dispatch,
    )
    runner = _connected_runner(
        tmp_path,
        budget=_budget_with(),
        recipe_executor=None,
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=None,
        request_id="dispatch-failure-request",
        session_key="isolated-session",
        planner_model_calls=1,
        planner_tokens=10,
    )

    diagnostic = ",".join(result.execution.rollback_reasons)
    assert (
        "dispatch:connected_dispatch_failed:ValueError:message_sha256="
    ) in diagnostic
    assert "provider dispatch failed" not in diagnostic
    assert "top-secret" not in diagnostic
    assert result.execution.final_content is None
    assert result.execution.side_effect_counts.total == 0


@pytest.mark.asyncio
@pytest.mark.offline
async def test_connected_primary_unsafe_effect_never_promotes_final(
    tmp_path,
    monkeypatch,
) -> None:
    async def unsafe_dispatch(_self, invocation, **_kwargs):
        result = NormalizedAssetResultV1(
            invocation_id=invocation.invocation_id,
            output_contract=invocation.output_contract,
            status=AssetResultStatus.RESOLVED,
            payload={"answer": "must-not-promote"},
            payload_hash=hashlib.sha256(b"unsafe").hexdigest(),
            effect_status=EffectStatus.CONFIRMATION_REQUIRED,
        )
        return AdapterResponse(
            invocation_id=invocation.invocation_id,
            status=AssetResultStatus.RESOLVED,
            input_payload_hash=invocation.payload_hash,
            effect_status=EffectStatus.CONFIRMATION_REQUIRED,
            result=result,
            dispatched=True,
        )

    monkeypatch.setattr(
        "simpleclaw.graph_runtime.shadow.GenericRecipeAdapter.dispatch",
        unsafe_dispatch,
    )
    runner = ConnectedShadowTurnRunner(
        facade=LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="primary",
            shadow_no_send=True,
            budget=_budget_with(),
            checkpoint_path=tmp_path / "unsafe-checkpoint.sqlite3",
        ),
        definitions=_definitions(),
        conversation_store=ConversationStore(tmp_path / "unsafe-conversation.db"),
    )
    result = await runner.run(
        plan=_plan(
            "recipe",
            "contract-fixture-workflow",
            ExecutionMode.DIRECT_ANSWER,
        ),
        legacy=None,
        request_id="unsafe-effect-request",
        session_key="unsafe-effect-session",
        planner_model_calls=0,
        planner_tokens=0,
    )

    assert result.execution.final_content is None
    assert result.execution.rollback_required is True
    assert "effect_not_safe" in result.execution.rollback_reasons
    assert result.execution.dispatch_trace.succeeded == 0


@pytest.mark.asyncio
@pytest.mark.offline
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
@pytest.mark.offline
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
@pytest.mark.offline
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
                artifact_id=canonical_artifact_id(
                    request_id, "V4가 만든 최종 응답"
                ),
                request_id=request_id,
                content="V4가 만든 최종 응답",
                outcome=TerminalOutcome.COMPLETED,
                content_hash=canonical_artifact_content_hash(
                    "V4가 만든 최종 응답"
                ),
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
    assert result.primary_delivery is not None
    assert result.primary_delivery.request_id == turn.turn_id
    assert result.primary_delivery.artifact_hash == (
        canonical_artifact_content_hash("V4가 만든 최종 응답")
    )
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
