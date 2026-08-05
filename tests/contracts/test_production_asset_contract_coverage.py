"""BIZ-579/BIZ-585 production read-only asset의 V4 contract coverage 회귀."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.tool_schemas import ToolScope, build_native_tool_registry
from simpleclaw.agent.turn_plan import AssetRef
from simpleclaw.capability import parse_owned_contract_metadata
from simpleclaw.evaluation.langgraph_v4_scenario_eval import classify_contract
from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.contracts_registry import (
    ContractRegistryError,
    build_contract_registry,
)
from simpleclaw.graph_runtime.shadow import ConnectedShadowTurnRunner
from simpleclaw.recipes.loader import discover_recipes, load_recipe
from simpleclaw.recipes.models import RecipeParseError
from simpleclaw.skills.discovery import discover_skills

ROOT = Path(__file__).parents[2]
PRODUCTION_SKILL_FIXTURES = ROOT / "tests/fixtures/production-skills"
SPORTS_RECIPE_FIXTURES = ROOT / "tests/fixtures/recipes"
READ_ONLY_SKILLS = {
    "gmail-skill",
    "google-news-search-skill",
    "kr-stock-skill",
    "naver-shopping-skill",
    "naver-sports-skill",
    "us-stock-skill",
}
MUTATION_SKILLS = {"google-calendar-skill"}
TARGET_SKILLS = READ_ONLY_SKILLS | MUTATION_SKILLS
TARGET_NATIVE_TOOLS = {"web_fetch", "web_search"}


def _sports_recipe():
    recipes = tuple(
        item
        for item in discover_recipes(SPORTS_RECIPE_FIXTURES)
        if item.name == "sports-live"
    )
    assert len(recipes) == 1
    return recipes[0]


def _production_definitions():
    skills = tuple(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"), PRODUCTION_SKILL_FIXTURES
        )
        if item.name in TARGET_SKILLS
    )
    assert {item.name for item in skills} == TARGET_SKILLS
    assert all(
        Path(item.skill_dir).is_relative_to(PRODUCTION_SKILL_FIXTURES)
        for item in skills
    )
    native_specs = tuple(
        spec
        for spec in build_native_tool_registry(scopes=(ToolScope.RUNTIME,))
        if spec.name in TARGET_NATIVE_TOOLS
    )
    return skills, native_specs


def _declared_schema_examples(registry, skill) -> tuple[dict[str, object], ...]:
    """Registry schema의 모든 example을 엄격히 canonical validation한다."""
    entry = registry.asset(AssetRefV1(type="skill", name=skill.name))
    assert entry is not None
    raw_examples = entry.input_descriptor.json_schema.get("examples")
    if not isinstance(raw_examples, list) or not raw_examples:
        raise ContractRegistryError("payload.safe_example_missing")

    canonical = []
    for example in raw_examples:
        if not isinstance(example, Mapping):
            raise ContractRegistryError("payload.declared_example_invalid")
        try:
            payload = registry.validate_canonical(
                entry.input_descriptor,
                example,
            ).payload
        except ContractRegistryError as exc:
            raise ContractRegistryError("payload.declared_example_invalid") from exc
        canonical.append(payload)
    return tuple(canonical)


def test_production_read_only_assets_have_complete_owned_contracts() -> None:
    skills, native_specs = _production_definitions()
    catalog = build_planner_catalog(skills=skills, native_specs=native_specs)
    refs = tuple(
        AssetRef(asset.asset_type, asset.name)
        for asset in catalog.assets
        if asset.name in READ_ONLY_SKILLS | TARGET_NATIVE_TOOLS
    )

    classification = classify_contract(catalog, refs)
    registry = build_contract_registry((*skills, *native_specs))

    assert classification.status == "read_only_complete", classification.issues
    assert len(refs) == 8
    assert len(registry.entries) == 9
    for ref in refs:
        entry = registry.asset(AssetRefV1(type=ref.asset_type, name=ref.name))
        asset = next(
            item
            for item in catalog.assets
            if (item.asset_type, item.name) == (ref.asset_type, ref.name)
        )
        assert entry is not None
        assert entry.snapshot.read_only is True
        assert entry.snapshot.side_effects is False
        assert entry.snapshot.declared_binding is not None
        assert asset.contract_owner == f"{ref.asset_type}:{ref.name}"
        assert entry.input_descriptor.ref.schema_hash == asset.input_schema_hash
        assert entry.output_descriptor.ref.schema_hash == asset.output_schema_hash
        assert asset.binding_identity == (
            f"{entry.snapshot.declared_binding.binding_id}:"
            f"{entry.snapshot.declared_binding.binding_hash}"
        )


def test_calendar_mixed_operation_contract_is_confirmation_safe() -> None:
    skills, _ = _production_definitions()
    calendar = next(item for item in skills if item.name == "google-calendar-skill")
    catalog = build_planner_catalog(skills=(calendar,), native_specs=())
    classification = classify_contract(
        catalog,
        (AssetRef("skill", "google-calendar-skill"),),
    )
    entry = build_contract_registry((calendar,)).entries[0]

    assert classification.status == "dispatch_denied"
    assert [item.error_code for item in classification.issues] == [
        "contract.not_read_only"
    ]
    assert entry.snapshot.read_only is False
    assert entry.snapshot.side_effects is True
    assert entry.snapshot.requires_confirmation is True


def test_native_web_contract_hashes_match_function_calling_schemas() -> None:
    _, native_specs = _production_definitions()

    for spec in native_specs:
        assert spec.input_contract is not None
        assert spec.output_contract is not None
        assert spec.argument_binding is not None
        assert spec.input_contract.json_schema == spec.definition.parameters
        assert spec.argument_binding.binding == {"strategy": "arguments"}
        assert len(spec.input_contract.schema_hash) == 64
        assert len(spec.output_contract.schema_hash) == 64
        assert len(spec.argument_binding.binding_hash) == 64


def test_skill_contract_rejects_non_json_cli_invocation() -> None:
    skills, _ = _production_definitions()
    registry = build_contract_registry(skills)

    for entry in registry.entries:
        if entry.snapshot.asset_ref.name not in {
            "google-news-search-skill",
            "kr-stock-skill",
            "us-stock-skill",
        }:
            continue
        with pytest.raises(ContractRegistryError, match="payload.schema_mismatch"):
            registry.validate_canonical(
                entry.input_descriptor,
                {"args": "market-summary"},
            )


def test_declared_skill_examples_validate_against_canonical_schemas() -> None:
    skills, _ = _production_definitions()
    registry = build_contract_registry(skills)

    for skill in skills:
        examples = _declared_schema_examples(registry, skill)
        assert len(examples) == 1
        assert examples[0]["args"]


@pytest.mark.parametrize("corruption", ("missing", "invalid"))
def test_declared_skill_example_gap_fails_closed(corruption: str) -> None:
    skills, _ = _production_definitions()
    target = next(item for item in skills if item.name == "naver-shopping-skill")
    assert target.input_contract is not None
    schema = target.input_contract.json_schema
    if corruption == "missing":
        schema.pop("examples")
        expected = "payload.safe_example_missing"
    else:
        schema["examples"] = [{"args": "purchase --sku forbidden"}]
        expected = "payload.declared_example_invalid"
    metadata = parse_owned_contract_metadata(
        {
            "contract_id": target.input_contract.contract_id,
            "version": target.input_contract.version,
            "owner_ref": {
                "type": target.input_contract.owner_type,
                "name": target.input_contract.owner_name,
            },
            "json_schema": schema,
        },
        source="corrupt-example",
    )
    assert metadata is not None
    definition = replace(target, input_contract=metadata)
    registry = build_contract_registry((definition,))

    with pytest.raises(ContractRegistryError, match=expected):
        _declared_schema_examples(registry, definition)


def test_sports_recipe_contract_identity_is_continuous_across_discovery() -> None:
    recipe = _sports_recipe()
    catalog = build_planner_catalog(recipes=(recipe,), native_specs=())
    ref = AssetRef("recipe", "sports-live")
    classification = classify_contract(catalog, (ref,))
    registry = build_contract_registry((recipe,))

    assert classification.status == "read_only_complete", classification.issues
    assert recipe.capability.input_contract == "query.v1"
    assert recipe.capability.output_contract == "asset_result.v1"
    assert recipe.input_contract is not None
    assert recipe.output_contract is not None
    assert recipe.contract_binding is not None
    assert recipe.input_contract.json_schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    assert recipe.step_bindings[0].binding == {
        "target_skill": "naver-sports-skill",
        "map": {"query": "args"},
    }

    asset = next(item for item in catalog.assets if item.name == "sports-live")
    entry = registry.asset(AssetRefV1(type="recipe", name="sports-live"))
    assert entry is not None
    assert asset.contract_owner == "recipe:sports-live"
    assert asset.input_contract_ref == "recipe.sports-live.input@1"
    assert asset.output_contract_ref == "recipe.sports-live.output@1"
    assert asset.input_schema_hash == recipe.input_contract.schema_hash
    assert asset.output_schema_hash == recipe.output_contract.schema_hash
    assert asset.binding_identity == (
        f"{recipe.contract_binding.binding_id}:"
        f"{recipe.contract_binding.binding_hash}"
    )
    assert entry.input_descriptor.ref.schema_hash == asset.input_schema_hash
    assert entry.output_descriptor.ref.schema_hash == asset.output_schema_hash
    assert entry.snapshot.declared_binding is not None
    assert (
        f"{entry.snapshot.declared_binding.binding_id}:"
        f"{entry.snapshot.declared_binding.binding_hash}"
    ) == asset.binding_identity
    assert entry.snapshot.read_only is True
    assert entry.snapshot.side_effects is False


def test_sports_recipe_delegate_resolves_production_skill_contract() -> None:
    recipe = _sports_recipe()
    skills, _ = _production_definitions()
    target = next(item for item in skills if item.name == "naver-sports-skill")
    catalog = build_planner_catalog(skills=(target,), recipes=(recipe,), native_specs=())
    registry = build_contract_registry((recipe, target))

    assert recipe.step_bindings[0].binding["target_skill"] == target.name
    target_asset = next(item for item in catalog.assets if item.name == target.name)
    target_entry = registry.asset(AssetRefV1(type="skill", name=target.name))
    assert target_entry is not None
    assert target_asset.contract_owner == "skill:naver-sports-skill"
    assert target_entry.input_descriptor.ref.schema_hash == target_asset.input_schema_hash
    assert target_entry.output_descriptor.ref.schema_hash == target_asset.output_schema_hash
    target_example = _declared_schema_examples(registry, target)[0]
    recipe_entry = registry.asset(AssetRefV1(type="recipe", name=recipe.name))
    assert recipe_entry is not None
    recipe_payload = registry.validate_canonical(
        recipe_entry.input_descriptor,
        {"query": target_example["args"]},
    ).payload
    mapped_payload = {
        target_name: recipe_payload[source_name]
        for source_name, target_name in recipe.step_bindings[0].binding["map"].items()
    }
    assert registry.validate_canonical(
        target_entry.input_descriptor,
        mapped_payload,
    ).payload == target_example


@pytest.mark.parametrize("corruption", ("missing_binding", "owner_mismatch"))
def test_production_skill_invalid_contract_fails_before_dispatch(
    tmp_path: Path,
    corruption: str,
) -> None:
    skills, _ = _production_definitions()
    target = next(item for item in skills if item.name == "naver-sports-skill")
    if corruption == "missing_binding":
        definition = replace(target, argument_binding=None)
        expected_error = "definition.contract_metadata_incomplete"
    else:
        assert target.output_contract is not None
        definition = replace(
            target,
            output_contract=replace(target.output_contract, owner_name="other-skill"),
        )
        expected_error = "definition.owner_mismatch"
    dispatch_count = 0

    async def executor(_definition, _bound_steps):
        nonlocal dispatch_count
        dispatch_count += 1
        return {}

    with pytest.raises(ContractRegistryError, match=expected_error):
        ConnectedShadowTurnRunner(
            facade=object(),
            definitions=(definition,),
            conversation_store=object(),
            recipe_executor=executor,
        )

    assert dispatch_count == 0


@pytest.mark.parametrize("corruption", ("missing_binding", "owner_mismatch"))
def test_sports_recipe_invalid_contract_fails_before_dispatch(
    tmp_path: Path,
    corruption: str,
) -> None:
    raw = yaml.safe_load(
        (SPORTS_RECIPE_FIXTURES / "sports-live/recipe.yaml").read_text(
            encoding="utf-8"
        )
    )
    recipe_path = tmp_path / "sports-live/recipe.yaml"
    recipe_path.parent.mkdir()
    if corruption == "missing_binding":
        raw.pop("step_bindings")
    else:
        raw["output_contract"]["owner_ref"]["name"] = "other-recipe"
    recipe_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    dispatch_count = 0

    async def executor(_definition, _bound_steps):
        nonlocal dispatch_count
        dispatch_count += 1
        return {}

    if corruption == "missing_binding":
        with pytest.raises(RecipeParseError, match="must declare input_contract"):
            load_recipe(recipe_path)
        definition = replace(_sports_recipe(), step_bindings=())
        expected_error = "definition.contract_metadata_incomplete"
    else:
        definition = load_recipe(recipe_path)
        expected_error = "definition.owner_mismatch"

    with pytest.raises(ContractRegistryError, match=expected_error):
        ConnectedShadowTurnRunner(
            facade=object(),
            definitions=(definition,),
            conversation_store=object(),
            recipe_executor=executor,
        )

    assert dispatch_count == 0
