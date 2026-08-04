from __future__ import annotations

import pytest

from simpleclaw.capability import (
    CapabilityMetadata,
    parse_owned_binding_metadata,
    parse_owned_contract_metadata,
)
from simpleclaw.graph_runtime.adapters.recipe import GenericRecipeAdapter
from simpleclaw.graph_runtime.contracts import AssetInvocationV1
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus
from simpleclaw.recipes.models import RecipeDefinition, RecipeResult
from simpleclaw.skills.models import SkillDefinition


def _contract(
    name: str,
    *,
    owner_type: str = "recipe",
    owner_name: str = "fixture-recipe",
):
    value = parse_owned_contract_metadata(
        {
            "contract_id": f"fixture.{name}",
            "version": "1",
            "owner_ref": {"type": owner_type, "name": owner_name},
            "json_schema": {
                "type": "object",
                "properties": {name: {"type": "string"}},
                "required": [name],
                "additionalProperties": False,
            },
        },
        source=name,
    )
    assert value is not None
    return value


def _recipe() -> RecipeDefinition:
    binding = parse_owned_binding_metadata(
        {
            "binding_id": "child.v1",
            "owner_ref": {"type": "recipe", "name": "fixture-recipe"},
            "binding": {
                "target_skill": "arbitrary-child",
                "map": {"orbit": "input"},
            },
        },
        source="binding",
    )
    assert binding is not None
    return RecipeDefinition(
        name="fixture-recipe",
        capability=CapabilityMetadata(declared=True, read_only=True, side_effects=False),
        input_contract=_contract("orbit"),
        output_contract=_contract("report"),
        step_bindings=(binding,),
    )


def _child_skill() -> SkillDefinition:
    binding = parse_owned_binding_metadata(
        {
            "binding_id": "argv.v1",
            "owner_ref": {"type": "skill", "name": "arbitrary-child"},
            "binding": {"strategy": "named", "order": ["input"]},
        },
        source="child-binding",
    )
    assert binding is not None
    return SkillDefinition(
        name="arbitrary-child",
        capability=CapabilityMetadata(declared=True, read_only=True, side_effects=False),
        input_contract=_contract(
            "input", owner_type="skill", owner_name="arbitrary-child"
        ),
        output_contract=_contract(
            "child_result", owner_type="skill", owner_name="arbitrary-child"
        ),
        argument_binding=binding,
    )


def _invocation(recipe: RecipeDefinition) -> tuple[object, AssetInvocationV1]:
    registry = build_contract_registry([recipe, _child_skill()])
    entry = registry.asset(
        next(
            item.snapshot.asset_ref
            for item in registry.entries
            if item.snapshot.asset_ref.type == "recipe"
        )
    )
    assert entry is not None
    canonical = registry.validate_canonical(entry.input_descriptor, {"orbit": "europa"})
    invocation = AssetInvocationV1(
        invocation_id="recipe-1",
        asset_ref=entry.snapshot.asset_ref,
        definition_fingerprint=recipe.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )
    return registry, invocation


@pytest.mark.asyncio
async def test_recipe_owned_mapping_is_deterministic_and_preserves_input_hash() -> None:
    recipe = _recipe()
    registry, invocation = _invocation(recipe)
    calls = []

    async def executor(exact_definition, bound_steps):
        calls.append((exact_definition, bound_steps))
        return {"report": "done"}

    response = await GenericRecipeAdapter(registry, recipe, executor).dispatch(invocation)

    assert calls[0][0] is recipe
    bound = calls[0][1][0]
    assert bound.binding_id == "child.v1"
    assert bound.target_skill == "arbitrary-child"
    assert bound.payload == {"input": "europa"}
    assert bound.source_payload_json == invocation.payload_json
    assert bound.source_payload_hash == invocation.payload_hash
    assert response.status is AssetResultStatus.RESOLVED
    assert response.input_payload_hash == invocation.payload_hash


@pytest.mark.asyncio
async def test_missing_mapping_source_fails_before_recipe_executor() -> None:
    recipe = _recipe()
    registry, invocation = _invocation(recipe)
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"report": "never"}

    bad = invocation.model_copy(update={"payload_json": "{}"})
    response = await GenericRecipeAdapter(registry, recipe, executor).dispatch(bad)

    assert calls == 0
    assert response.status is AssetResultStatus.FAILED
    assert response.error_code is not None
    assert response.error_code.startswith("payload.required")


@pytest.mark.asyncio
async def test_failed_recipe_result_is_fail_closed_without_redispatch() -> None:
    recipe = _recipe()
    recipe.capability = CapabilityMetadata(
        declared=True,
        read_only=False,
        side_effects=True,
        requires_confirmation=True,
    )
    registry, invocation = _invocation(recipe)
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return RecipeResult(recipe_name=recipe.name, success=False, error="failed")

    adapter = GenericRecipeAdapter(registry, recipe, executor)
    failed = await adapter.dispatch(invocation, authorized=True)
    blocked = await adapter.dispatch(
        invocation,
        authorized=True,
        dispatch_started=True,
    )

    assert calls == 1
    assert failed.status is AssetResultStatus.BLOCKED
    assert failed.effect_status is EffectStatus.UNKNOWN
    assert failed.dispatched is True
    assert failed.error_code == "executor.failed"
    assert blocked.status is AssetResultStatus.BLOCKED
    assert blocked.dispatched is False
    assert blocked.error_code == "effect.redispatch_blocked"
