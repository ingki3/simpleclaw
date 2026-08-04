from __future__ import annotations

import inspect

import pytest

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


@pytest.mark.asyncio
async def test_planner_canonical_bytes_reach_recipe_binding_unchanged() -> None:
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
