"""BIZ-566 discovery-built contract registry의 identity/validation 회귀."""

from __future__ import annotations

import inspect
from dataclasses import replace

import pytest

from simpleclaw.capability import (
    CapabilityMetadata,
    parse_owned_binding_metadata,
    parse_owned_contract_metadata,
)
from simpleclaw.graph_runtime.contracts import (
    COMPOSITION_FIELDS_EXTENSION,
    STRUCTURAL_EVIDENCE_RELATIONS_EXTENSION,
    AssetBindingRefV1,
    AssetRefV1,
)
from simpleclaw.graph_runtime.contracts_registry import (
    ContractRegistryError,
    build_contract_registry,
)
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition


def _contract(
    name: str,
    *,
    owner: str = "fixture-skill",
    owner_type: str = "skill",
    additional_properties: object = False,
    schema_extensions: dict[str, object] | None = None,
):
    schema = {
        "type": "object",
        "properties": {name: {"type": "string", "minLength": 1}},
        "required": [name],
        "additionalProperties": additional_properties,
    }
    if schema_extensions is not None:
        schema.update(schema_extensions)
    value = parse_owned_contract_metadata(
        {
            "contract_id": f"fixture.{name}",
            "version": "1",
            "owner_ref": {"type": owner_type, "name": owner},
            "json_schema": schema,
        },
        source=name,
    )
    assert value is not None
    return value


def _binding(
    *,
    owner: str = "fixture-skill",
    owner_type: str = "skill",
    binding_id: str = "argv.v1",
):
    value = parse_owned_binding_metadata(
        {
            "binding_id": binding_id,
            "owner_ref": {"type": owner_type, "name": owner},
            "binding": {"strategy": "named", "order": ["nebula_key"]},
        },
        source="argument_binding",
    )
    assert value is not None
    return value


def _skill() -> SkillDefinition:
    return SkillDefinition(
        name="fixture-skill",
        capability=CapabilityMetadata(
            declared=True,
            read_only=True,
            side_effects=False,
        ),
        input_contract=_contract("nebula_key"),
        output_contract=_contract("result_token"),
        argument_binding=_binding(),
    )


def test_registry_resolves_owner_version_schema_and_validates_canonical_payload():
    skill = _skill()
    registry = build_contract_registry([skill])
    entry = registry.entries[0]

    descriptor = registry.resolve(
        entry.input_descriptor.ref,
        owner=AssetRefV1(type="skill", name="fixture-skill"),
    )
    canonical = registry.validate_canonical(descriptor, {"nebula_key": "value"})

    assert descriptor.ref.contract_id == "fixture.nebula_key"
    assert descriptor.ref.version == "1"
    assert len(descriptor.ref.schema_hash) == 64
    assert canonical.payload == {"nebula_key": "value"}
    assert len(canonical.payload_hash) == 64
    with pytest.raises(ContractRegistryError, match="payload.unknown_key"):
        registry.validate_canonical(
            descriptor,
            {"nebula_key": "value", "core_guessed_key": "blocked"},
        )


def test_duplicate_asset_owner_and_metadata_hash_conflicts_are_rejected():
    first = _skill()
    with pytest.raises(ContractRegistryError, match="definition.duplicate_asset"):
        build_contract_registry([first, _skill()])

    wrong_owner = replace(first, input_contract=_contract("nebula_key", owner="other"))
    with pytest.raises(ContractRegistryError, match="definition.owner_mismatch"):
        build_contract_registry([wrong_owner])

    bad_hash = replace(
        first,
        input_contract=replace(first.input_contract, schema_hash="stale"),
    )
    with pytest.raises(ContractRegistryError, match="schema_hash_mismatch"):
        build_contract_registry([bad_hash])


def test_malformed_additional_properties_is_rejected_before_candidate_build():
    malformed = replace(
        _skill(),
        input_contract=_contract(
            "nebula_key",
            additional_properties="false",
        ),
    )

    with pytest.raises(ContractRegistryError, match="schema.invalid"):
        build_contract_registry([malformed])


@pytest.mark.parametrize(
    "relation",
    [
        {
            "when": {"path": "record_state", "equals": "ready"},
            "evidence_fields": ["record_state"],
            "identity_fields": "record_state",
        },
        {
            "when": {"path": "record_state", "equals": []},
            "evidence_fields": ["record_state"],
        },
    ],
    ids=("non_array_identity", "container_equals"),
)
def test_invalid_relation_types_are_normalized_at_registry_boundary(
    relation: dict[str, object],
) -> None:
    malformed = replace(
        _skill(),
        output_contract=_contract(
            "record_state",
            schema_extensions={
                COMPOSITION_FIELDS_EXTENSION: ["record_state"],
                STRUCTURAL_EVIDENCE_RELATIONS_EXTENSION: [relation],
            },
        ),
    )

    with pytest.raises(
        ContractRegistryError,
        match="^schema.invalid_composition_fields$",
    ) as captured:
        build_contract_registry([malformed])

    assert captured.value.code == "schema.invalid_composition_fields"
    assert isinstance(captured.value.__cause__, ValueError | TypeError)


def test_second_recipe_step_binding_owner_mismatch_is_rejected_by_registry():
    recipe = RecipeDefinition(
        name="fixture-recipe",
        capability=CapabilityMetadata(
            declared=True,
            read_only=True,
            side_effects=False,
        ),
        input_contract=_contract(
            "task_input",
            owner="fixture-recipe",
            owner_type="recipe",
        ),
        output_contract=_contract(
            "task_output",
            owner="fixture-recipe",
            owner_type="recipe",
        ),
        step_bindings=(
            _binding(
                owner="fixture-recipe",
                owner_type="recipe",
                binding_id="step-one.v1",
            ),
            _binding(
                owner="other-owner",
                owner_type="recipe",
                binding_id="step-two.v1",
            ),
        ),
    )

    with pytest.raises(ContractRegistryError, match="definition.owner_mismatch"):
        build_contract_registry([recipe])


def test_definition_and_binding_drift_produce_no_dispatch_candidate():
    skill = _skill()
    registry = build_contract_registry([skill])
    entry = registry.entries[0]
    snapshot = entry.snapshot
    binding = snapshot.declared_binding
    assert binding is not None

    common = {
        "asset_ref": snapshot.asset_ref,
        "definition_fingerprint": snapshot.definition_fingerprint,
        "input_contract": entry.input_descriptor.ref,
        "output_contract": entry.output_descriptor.ref,
        "binding_ref": binding,
        "registry_fingerprint": registry.fingerprint,
    }
    assert registry.dispatch_candidate(**common) == entry
    assert registry.dispatch_candidate(
        **{**common, "definition_fingerprint": "drift"}
    ) is None
    skill.description = "definition changed after snapshot"
    assert skill.definition_fingerprint != snapshot.definition_fingerprint
    assert registry.dispatch_candidate(
        **{**common, "definition_fingerprint": skill.definition_fingerprint}
    ) is None
    assert registry.dispatch_candidate(
        **{
            **common,
            "binding_ref": AssetBindingRefV1(
                owner_ref=binding.owner_ref,
                binding_id=binding.binding_id,
                binding_hash="drift",
            ),
        }
    ) is None
    assert registry.dispatch_candidate(
        **{**common, "registry_fingerprint": "stale"}
    ) is None


def test_legacy_definition_is_discovered_but_not_typed_registry_candidate():
    legacy = SkillDefinition(name="legacy")

    registry = build_contract_registry([legacy])

    assert registry.entries == ()
    assert len(registry.fingerprint) == 64


def test_registry_core_has_no_static_contract_id_or_concrete_asset_import():
    from simpleclaw.graph_runtime import contracts_registry

    source = inspect.getsource(contracts_registry)

    assert "simpleclaw.skills" not in source
    assert "simpleclaw.recipes" not in source
    assert "fixture." not in source
