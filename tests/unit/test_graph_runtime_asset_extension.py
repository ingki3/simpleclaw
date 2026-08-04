"""새 asset contract가 Core 수정 없이 discovery/catalog/registry에 들어오는지 검증."""

from __future__ import annotations

import json

from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills


def _contract_yaml(contract_id: str, owner_type: str, owner_name: str, key: str) -> str:
    return f"""
  contract_id: {contract_id}
  version: '1'
  owner_ref:
    type: {owner_type}
    name: {owner_name}
  json_schema:
    type: object
    properties:
      {key}:
        type: string
    required: [{key}]
    additionalProperties: false
"""


def test_new_skill_fixture_contract_is_discovered_without_core_registration(tmp_path):
    local = tmp_path / "skills"
    skill_dir = local / "cosmic-extension"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: cosmic-extension
description: Synthetic domain-neutral extension.
capability:
  read_only: true
  side_effects: false
input_contract:
"""
        + _contract_yaml("cosmic.input", "skill", "cosmic-extension", "quasar")
        + """output_contract:
"""
        + _contract_yaml("cosmic.output", "skill", "cosmic-extension", "pulsar")
        + """argument_binding:
  binding_id: named.v1
  owner_ref:
    type: skill
    name: cosmic-extension
  binding:
    strategy: named
    fields: [quasar]
---
""",
        encoding="utf-8",
    )

    skills = discover_skills(local, tmp_path / "global")
    registry = build_contract_registry(skills)
    catalog = build_planner_catalog(skills=skills, native_specs=[])
    prompt = json.loads(catalog.to_prompt_json())
    asset = next(item for item in prompt if item["type"] == "skill")

    assert [item.name for item in skills] == ["cosmic-extension"]
    assert registry.entries[0].snapshot.asset_ref.name == "cosmic-extension"
    assert asset["contract_owner"] == "skill:cosmic-extension"
    assert len(asset["input_schema_hash"]) == 64
    assert len(asset["binding_identity"].split(":", 1)[1]) == 64
    assert "quasar" not in json.dumps(asset)
    assert "json_schema" not in asset


def test_recipe_contract_and_step_bindings_are_opt_in_and_legacy_stays_loadable(
    tmp_path,
):
    recipes = tmp_path / "recipes"
    typed_dir = recipes / "typed"
    typed_dir.mkdir(parents=True)
    (typed_dir / "recipe.yaml").write_text(
        "name: cosmic-recipe\n"
        "description: Synthetic recipe.\n"
        "capability:\n  read_only: true\n  side_effects: false\n"
        "input_contract:\n"
        + _contract_yaml("cosmic.task.input", "recipe", "cosmic-recipe", "orbit")
        + "output_contract:\n"
        + _contract_yaml("cosmic.task.output", "recipe", "cosmic-recipe", "signal")
        + "step_bindings:\n"
        "  - binding_id: step-one.v1\n"
        "    owner_ref:\n      type: recipe\n      name: cosmic-recipe\n"
        "    binding:\n      target_skill: arbitrary-child\n      map: {orbit: input}\n",
        encoding="utf-8",
    )
    legacy_dir = recipes / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "recipe.yaml").write_text(
        "name: legacy-recipe\ndescription: Legacy remains valid.\n",
        encoding="utf-8",
    )

    discovered = discover_recipes(recipes)
    registry = build_contract_registry(discovered)

    assert {item.name for item in discovered} == {"cosmic-recipe", "legacy-recipe"}
    assert [item.snapshot.asset_ref.name for item in registry.entries] == [
        "cosmic-recipe"
    ]

