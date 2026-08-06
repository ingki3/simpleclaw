"""BIZ-609 — production sports-live installer contract."""

from __future__ import annotations

from pathlib import Path

from scripts.install_sports_live_recipe import CANONICAL_RECIPE, install
from simpleclaw.recipes.loader import discover_recipes


def test_installer_writes_canonical_recipe_with_owned_binding(tmp_path: Path) -> None:
    recipe_dir = install(tmp_path / "recipes")
    installed = recipe_dir / "recipe.yaml"

    assert installed.read_bytes() == CANONICAL_RECIPE.read_bytes()
    recipes = discover_recipes(tmp_path / "recipes")
    assert len(recipes) == 1
    recipe = recipes[0]
    assert recipe.name == "sports-live"
    assert recipe.input_contract is not None
    assert recipe.output_contract is not None
    assert recipe.contract_binding is not None
    assert recipe.input_contract.owner_name == recipe.name
    assert recipe.output_contract.owner_name == recipe.name
    assert recipe.contract_binding.owner_name == recipe.name
