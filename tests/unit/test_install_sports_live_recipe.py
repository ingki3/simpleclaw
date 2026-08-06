"""BIZ-609 — production sports-live installer contract."""

from __future__ import annotations

from pathlib import Path

from scripts.install_sports_live_recipe import CANONICAL_RECIPE, install, main
from simpleclaw.config_sections.agents import load_recipes_config
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


def test_no_arg_installer_matches_runtime_default_in_isolated_home(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert main([]) == 0

    runtime_dir = Path(
        load_recipes_config(home / ".simpleclaw/config.yaml")["dir"]
    ).expanduser()
    installed = runtime_dir / "sports-live" / "recipe.yaml"
    assert installed.read_bytes() == CANONICAL_RECIPE.read_bytes()
    output = capsys.readouterr().out
    assert str(installed.parent) in output
    assert "source=" in output
    assert "manifest_sha256=" in output


def test_no_arg_installer_uses_configured_runtime_directory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    config = home / ".simpleclaw/config.yaml"
    configured_dir = tmp_path / "custom-recipes"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"recipes:\n  dir: {configured_dir}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    assert main([]) == 0

    installed = configured_dir / "sports-live" / "recipe.yaml"
    assert installed.read_bytes() == CANONICAL_RECIPE.read_bytes()
    assert str(installed.parent) in capsys.readouterr().out


def test_installer_is_idempotent(tmp_path: Path) -> None:
    recipes_dir = tmp_path / "recipes"

    first = install(recipes_dir)
    first_bytes = (first / "recipe.yaml").read_bytes()
    second = install(recipes_dir)

    assert second == first
    assert (second / "recipe.yaml").read_bytes() == first_bytes


def test_installer_repairs_recipe_bytes_and_declared_mode(tmp_path: Path) -> None:
    """recipe payload와 비실행 권한 drift를 재설치로 함께 복구한다."""
    recipes_dir = tmp_path / "recipes"
    installed = install(recipes_dir) / "recipe.yaml"

    installed.write_bytes(b"drifted recipe\n")
    installed.chmod(0o755)
    install(recipes_dir)

    assert installed.read_bytes() == CANONICAL_RECIPE.read_bytes()
    assert installed.stat().st_mode & 0o777 == 0o644
