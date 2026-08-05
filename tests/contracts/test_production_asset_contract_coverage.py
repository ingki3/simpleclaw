"""BIZ-579 production read-only asset의 V4 contract coverage 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.tool_schemas import ToolScope, build_native_tool_registry
from simpleclaw.agent.turn_plan import AssetRef
from simpleclaw.evaluation.langgraph_v4_scenario_eval import classify_contract
from simpleclaw.graph_runtime.contracts_registry import (
    ContractRegistryError,
    build_contract_registry,
)
from simpleclaw.skills.discovery import discover_skills

ROOT = Path(__file__).parents[2]
PRODUCTION_SKILL_FIXTURES = ROOT / "tests/fixtures/production-skills"
TARGET_SKILLS = {"google-news-search-skill", "kr-stock-skill"}
TARGET_NATIVE_TOOLS = {"web_fetch", "web_search"}


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


def test_four_reported_asset_groups_have_complete_owned_contracts() -> None:
    skills, native_specs = _production_definitions()
    catalog = build_planner_catalog(skills=skills, native_specs=native_specs)
    refs = tuple(
        AssetRef(asset.asset_type, asset.name)
        for asset in catalog.assets
        if asset.name in TARGET_SKILLS | TARGET_NATIVE_TOOLS
    )

    classification = classify_contract(catalog, refs)
    registry = build_contract_registry((*skills, *native_specs))

    assert classification.status == "read_only_complete", classification.issues
    assert len(refs) == 4
    assert len(registry.entries) == 4
    assert all(entry.snapshot.read_only for entry in registry.entries)
    assert all(not entry.snapshot.side_effects for entry in registry.entries)
    assert all(entry.snapshot.declared_binding for entry in registry.entries)


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
        with pytest.raises(ContractRegistryError, match="payload.schema_mismatch"):
            registry.validate_canonical(
                entry.input_descriptor,
                {"args": "market-summary"},
            )
