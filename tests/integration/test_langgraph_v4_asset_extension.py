"""Repo fixture를 통한 LangGraph V4 domain-neutral asset 확장 회귀."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from simpleclaw.capability import parse_owned_binding_metadata
from simpleclaw.graph_runtime.adapters.recipe import GenericRecipeAdapter
from simpleclaw.graph_runtime.adapters.skill import GenericSkillAdapter
from simpleclaw.graph_runtime.contracts import (
    AssetBindingRefV1,
    AssetInvocationV1,
    AssetRefV1,
)
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.status import AssetResultStatus
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

REPO_ROOT = Path(__file__).parents[2]
FIXTURE_RECIPE = REPO_ROOT / "tests/fixtures/recipes/contract-fixture-workflow"
FIXTURE_SKILL = REPO_ROOT / "tests/fixtures/skills/contract-fixture-step"
CORE_ROOT = REPO_ROOT / "src/simpleclaw/graph_runtime"


def _materialize_assets(tmp_path: Path) -> tuple[Path, Path]:
    """Repo fixture만 temp discovery root로 복사해 live asset 접근을 차단한다."""
    recipes = tmp_path / "recipes"
    skills = tmp_path / "skills"
    shutil.copytree(FIXTURE_RECIPE, recipes / FIXTURE_RECIPE.name)
    shutil.copytree(FIXTURE_SKILL, skills / FIXTURE_SKILL.name)
    return recipes, skills


def _definitions(recipes: Path, skills: Path):
    """동일 temp snapshot에서 Recipe와 Skill definition을 함께 발견한다."""
    discovered_recipes = discover_recipes(recipes)
    discovered_skills = discover_skills(skills, skills.parent / "global-skills")
    return discovered_recipes, discovered_skills


def _entry(registry, asset_type: str, name: str):
    """테스트가 payload key를 Core lookup 규칙으로 승격하지 않게 owner로 조회한다."""
    entry = registry.asset(AssetRefV1(type=asset_type, name=name))
    assert entry is not None
    return entry


def _invocation(registry, definition, payload: dict[str, str]) -> AssetInvocationV1:
    """발견된 descriptor가 만든 canonical payload로 exact invocation을 구성한다."""
    entry = _entry(registry, definition.contract_asset_type, definition.name)
    canonical = registry.validate_canonical(entry.input_descriptor, payload)
    return AssetInvocationV1(
        invocation_id=f"invoke-{definition.name}",
        asset_ref=entry.snapshot.asset_ref,
        definition_fingerprint=definition.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )


def _core_digest() -> str:
    """확장 fixture 수명주기 동안 Core source가 바뀌지 않았음을 비교한다."""
    digest = hashlib.sha256()
    for path in sorted(CORE_ROOT.rglob("*.py")):
        digest.update(path.relative_to(CORE_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.asyncio
async def test_repo_fixture_discovers_validates_and_dispatches_without_core_change(
    tmp_path: Path,
) -> None:
    """Planner payload부터 Recipe binding과 Skill argv까지 hash/identity를 보존한다."""
    before = _core_digest()
    recipes_dir, skills_dir = _materialize_assets(tmp_path)
    recipes, skills = _definitions(recipes_dir, skills_dir)
    recipe = next(item for item in recipes if item.name == "contract-fixture-workflow")
    skill = next(item for item in skills if item.name == "contract-fixture-step")
    registry = build_contract_registry([*recipes, *skills])
    invocation = _invocation(registry, recipe, {"fixture_key": "fixture-value"})
    recipe_calls = []

    async def recipe_executor(exact_definition, bound_steps):
        recipe_calls.append((exact_definition, bound_steps))
        return {"fixture_result": "recipe-ok"}

    recipe_response = await GenericRecipeAdapter(
        registry, recipe, recipe_executor
    ).dispatch(invocation)

    assert recipe_response.status is AssetResultStatus.RESOLVED
    assert recipe_response.input_payload_hash == invocation.payload_hash
    assert recipe_calls[0][0] is recipe
    bound = recipe_calls[0][1][0]
    assert bound.source_payload_json == invocation.payload_json
    assert bound.source_payload_hash == invocation.payload_hash
    assert bound.payload == {"operation_value": "fixture-value"}

    skill_entry = _entry(registry, "skill", skill.name)
    skill_invocation = AssetInvocationV1(
        invocation_id="invoke-contract-fixture-step",
        asset_ref=skill_entry.snapshot.asset_ref,
        definition_fingerprint=skill.definition_fingerprint,
        input_contract=bound.input_contract,
        payload=bound.payload,
        payload_hash=bound.payload_hash,
        output_contract=skill_entry.output_descriptor.ref,
    )
    skill_calls = []

    async def skill_executor(exact_definition, argv):
        skill_calls.append((exact_definition, argv))
        return {"operation_result": "skill-ok"}

    skill_response = await GenericSkillAdapter(
        registry, skill, skill_executor
    ).dispatch(skill_invocation)

    assert skill_calls == [(skill, ["--operation-value", "fixture-value"])]
    assert skill_response.status is AssetResultStatus.RESOLVED
    assert skill_response.input_payload_hash == bound.payload_hash
    assert _core_digest() == before


def test_fixture_can_be_replaced_and_deleted_from_temp_discovery(tmp_path: Path) -> None:
    """임의 contract/name/key 교체와 삭제가 Core 등록 없이 snapshot에 반영된다."""
    before = _core_digest()
    recipes_dir, skills_dir = _materialize_assets(tmp_path)
    recipe_path = recipes_dir / FIXTURE_RECIPE.name / "recipe.yaml"
    skill_path = skills_dir / FIXTURE_SKILL.name / "SKILL.md"
    replacements = {
        "contract-fixture-workflow": "alternate-workflow",
        "contract-fixture-step": "alternate-step",
        "fixture_key": "comet_token",
        "operation_value": "translated_token",
    }
    for path in (recipe_path, skill_path):
        content = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            content = content.replace(old, new)
        path.write_text(content, encoding="utf-8")

    recipes, skills = _definitions(recipes_dir, skills_dir)
    registry = build_contract_registry([*recipes, *skills])

    assert {entry.snapshot.asset_ref.name for entry in registry.entries} == {
        "alternate-workflow",
        "alternate-step",
    }
    recipe = next(item for item in recipes if item.name == "alternate-workflow")
    invocation = _invocation(registry, recipe, {"comet_token": "new-value"})
    assert invocation.payload == {"comet_token": "new-value"}

    shutil.rmtree(recipes_dir / FIXTURE_RECIPE.name)
    recipes, skills = _definitions(recipes_dir, skills_dir)
    assert {item.name for item in recipes} == set()
    assert {item.name for item in skills} == {"alternate-step"}

    shutil.rmtree(skills_dir / FIXTURE_SKILL.name)
    recipes, skills = _definitions(recipes_dir, skills_dir)
    assert build_contract_registry([*recipes, *skills]).entries == ()
    assert _core_digest() == before


@pytest.mark.asyncio
async def test_identity_binding_and_payload_mutations_dispatch_zero(tmp_path: Path) -> None:
    """Owner/schema/definition/binding/payload drift를 executor 전에 차단한다."""
    recipes_dir, skills_dir = _materialize_assets(tmp_path)
    recipes, skills = _definitions(recipes_dir, skills_dir)
    recipe = next(item for item in recipes if item.name == "contract-fixture-workflow")
    registry = build_contract_registry([*recipes, *skills])
    invocation = _invocation(registry, recipe, {"fixture_key": "fixture-value"})
    calls = 0

    async def executor(_definition, _bound_steps):
        nonlocal calls
        calls += 1
        return {"fixture_result": "never"}

    adapter = GenericRecipeAdapter(registry, recipe, executor)
    wrong_owner = invocation.model_copy(
        update={
            "input_contract": invocation.input_contract.model_copy(
                update={"owner_ref": AssetRefV1(type="recipe", name="other-owner")}
            )
        }
    )
    wrong_schema = invocation.model_copy(
        update={
            "input_contract": invocation.input_contract.model_copy(
                update={"schema_hash": "0" * 64}
            )
        }
    )
    wrong_definition = invocation.model_copy(
        update={"definition_fingerprint": "1" * 64}
    )
    changed_payload = invocation.model_copy(
        update={"payload_json": invocation.payload_json.replace("value", "valuf")}
    )

    responses = [
        await adapter.dispatch(candidate)
        for candidate in (
            wrong_owner,
            wrong_schema,
            wrong_definition,
            changed_payload,
        )
    ]

    entry = _entry(registry, "recipe", recipe.name)
    binding = entry.snapshot.declared_binding
    assert binding is not None
    swapped_binding = AssetBindingRefV1(
        owner_ref=binding.owner_ref,
        binding_id=binding.binding_id,
        binding_hash="2" * 64,
    )
    assert registry.dispatch_candidate(
        asset_ref=entry.snapshot.asset_ref,
        definition_fingerprint=entry.snapshot.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        output_contract=entry.output_descriptor.ref,
        binding_ref=swapped_binding,
        registry_fingerprint=registry.fingerprint,
    ) is None
    assert calls == 0
    assert [item.error_code for item in responses[:3]] == [
        "definition.drift",
        "definition.drift",
        "definition.drift",
    ]
    assert responses[3].error_code == "payload.hash_mismatch"


def test_recipe_binding_swap_changes_definition_and_registry_identity(
    tmp_path: Path,
) -> None:
    """Asset-owned binding 교체는 기존 snapshot과 호환되지 않는 새 정의가 된다."""
    recipes_dir, skills_dir = _materialize_assets(tmp_path)
    recipes, skills = _definitions(recipes_dir, skills_dir)
    recipe = next(item for item in recipes if item.name == "contract-fixture-workflow")
    original_fingerprint = recipe.definition_fingerprint
    changed = parse_owned_binding_metadata(
        {
            "binding_id": "fixture-step.v2",
            "owner_ref": {"type": "recipe", "name": recipe.name},
            "binding": {
                "target_skill": "contract-fixture-step",
                "map": {"fixture_key": "operation_value"},
            },
        },
        source="mutation",
    )
    assert changed is not None
    mutated = replace(recipe, step_bindings=(changed,))
    original = build_contract_registry([recipe, *skills])
    replacement = build_contract_registry([mutated, *skills])

    assert mutated.definition_fingerprint != original_fingerprint
    assert replacement.fingerprint != original.fingerprint
    assert (
        replacement.asset(AssetRefV1(type="recipe", name=recipe.name))
        .snapshot.declared_binding.binding_id
        == "step_bindings"
    )
