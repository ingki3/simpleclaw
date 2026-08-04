"""LangGraph V4 shadow/no-send와 read-only canary gate 회귀."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from simpleclaw.graph_runtime.contracts import (
    AssetInvocationV1,
    AssetRefV1,
    DeliveryIntentV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.runtime import (
    InMemoryDeliveryJournal,
    InMemoryPersistenceJournal,
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    PersistenceRuntime,
    ShadowBudgetUsageV1,
    ShadowNoSendConfigurationError,
    ShadowRunTelemetryV1,
    ShadowSideEffectCountsV1,
    compare_shadow_run,
    evaluate_read_only_canary,
)
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    DeliveryStatus,
    EffectStatus,
    InvocationStatus,
    TerminalOutcome,
)
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
