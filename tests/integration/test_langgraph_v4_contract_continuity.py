from __future__ import annotations

import inspect

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
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
from simpleclaw.capability import (
    CapabilityMetadata,
    parse_owned_binding_metadata,
    parse_owned_contract_metadata,
)
from simpleclaw.graph_runtime.adapters import recipe, skill
from simpleclaw.graph_runtime.adapters.recipe import GenericRecipeAdapter
from simpleclaw.graph_runtime.contracts import AssetInvocationV1, AssetRefV1
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition


def test_generic_adapter_core_has_no_domain_asset_or_nested_llm_path() -> None:
    source = inspect.getsource(skill) + inspect.getsource(recipe)

    assert "nested_llm" not in source
    assert "fixture-recipe" not in source
    assert "fixture-skill" not in source
    assert "graph_runtime/adapters/<domain>" not in source


def _contract(*, owner_type: str, owner_name: str, key: str):
    value = parse_owned_contract_metadata(
        {
            "contract_id": f"{owner_name}.{key}",
            "version": "1",
            "owner_ref": {"type": owner_type, "name": owner_name},
            "json_schema": {
                "type": "object",
                "properties": {key: {"type": "string"}},
                "required": [key],
                "additionalProperties": False,
            },
        },
        source=key,
    )
    assert value is not None
    return value


def _binding(*, owner_type: str, owner_name: str, binding_id: str, value):
    parsed = parse_owned_binding_metadata(
        {
            "binding_id": binding_id,
            "owner_ref": {"type": owner_type, "name": owner_name},
            "binding": value,
        },
        source=binding_id,
    )
    assert parsed is not None
    return parsed


def _definitions() -> tuple[RecipeDefinition, SkillDefinition]:
    child = SkillDefinition(
        name="unseen-child",
        capability=CapabilityMetadata(declared=True, read_only=True, side_effects=False),
        input_contract=_contract(
            owner_type="skill", owner_name="unseen-child", key="child_input"
        ),
        output_contract=_contract(
            owner_type="skill", owner_name="unseen-child", key="child_output"
        ),
        argument_binding=_binding(
            owner_type="skill",
            owner_name="unseen-child",
            binding_id="argv.v1",
            value={"strategy": "named", "order": ["child_input"]},
        ),
    )
    definition = RecipeDefinition(
        name="unseen-recipe",
        capability=CapabilityMetadata(declared=True, read_only=True, side_effects=False),
        input_contract=_contract(
            owner_type="recipe", owner_name="unseen-recipe", key="opaque_task"
        ),
        output_contract=_contract(
            owner_type="recipe", owner_name="unseen-recipe", key="opaque_result"
        ),
        step_bindings=(
            _binding(
                owner_type="recipe",
                owner_name="unseen-recipe",
                binding_id="child.v1",
                value={
                    "target_skill": "unseen-child",
                    "map": {"opaque_task": "child_input"},
                },
            ),
        ),
    )
    return definition, child


def test_plan_gate_seals_exact_catalog_definition_for_registry_continuity() -> None:
    definition, child = _definitions()
    catalog = build_planner_catalog(
        skills=(child,),
        recipes=(definition,),
        native_specs=(),
    )
    owner = AssetRef(asset_type="recipe", name=definition.name)
    plan = UnifiedTurnPlan(
        original_text="opaque request",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="opaque request",
        ),
        clarification=ClarificationPlan(required=False),
        domains=(),
        intents=(),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="none",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=owner,
            allowed_assets=(owner,),
        ),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.PARTIAL,
            primary_asset=owner,
            supporting_assets=(owner,),
        ),
        confidence=1.0,
        decision_summary="contract continuity",
        catalog_fingerprint=catalog.fingerprint,
    )

    gate = PlanGate().evaluate(
        plan,
        candidates=ContextCandidateSet((), 0, False),
        catalog=catalog,
    )
    registry = build_contract_registry((definition, child))
    entry = registry.asset(AssetRefV1(type="recipe", name=definition.name))
    catalog_asset = catalog.exact_asset("recipe", definition.name)

    assert gate.status is GateStatus.PASS
    assert gate.effective_plan is not None
    assert entry is not None
    assert catalog_asset is not None
    assert (
        gate.effective_plan.approved_asset_fingerprint
        == catalog_asset.definition_fingerprint
        == entry.snapshot.definition_fingerprint
        == definition.definition_fingerprint
    )


@pytest.mark.asyncio
async def test_planner_canonical_bytes_reach_recipe_binding_unchanged() -> None:
    definition, child = _definitions()
    registry = build_contract_registry([definition, child])
    owner = AssetRefV1(type="recipe", name="unseen-recipe")
    entry = registry.asset(owner)
    assert entry is not None
    canonical = registry.validate_canonical(
        entry.input_descriptor,
        {"opaque_task": "별빛"},
    )
    invocation = AssetInvocationV1(
        invocation_id="invoke-continuity",
        asset_ref=owner,
        definition_fingerprint=definition.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )
    captured = []

    async def executor(exact_definition, bound_steps):
        captured.append((exact_definition, bound_steps))
        return {"opaque_result": "ok"}

    response = await GenericRecipeAdapter(registry, definition, executor).dispatch(
        invocation
    )

    bound = captured[0][1][0]
    assert captured[0][0] is definition
    assert bound.source_payload_json == canonical.payload_json == invocation.payload_json
    assert bound.source_payload_hash == canonical.payload_hash == invocation.payload_hash
    assert bound.input_contract == registry.asset(bound.target_asset).input_descriptor.ref
    assert response.input_payload_hash == canonical.payload_hash
