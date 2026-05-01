"""Tests for recipe loader."""

from pathlib import Path

import pytest

from simpleclaw.recipes.loader import discover_recipes, load_recipe
from simpleclaw.recipes.models import OnErrorPolicy, RecipeParseError, StepType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "recipes"


class TestRecipeLoader:
    def test_load_valid_recipe(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        assert recipe.name == "daily-report"
        assert recipe.description != ""
        assert len(recipe.parameters) == 2
        assert len(recipe.steps) == 2

    def test_parameters_parsed(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        date_param = next(p for p in recipe.parameters if p.name == "date")
        assert date_param.required is True
        format_param = next(p for p in recipe.parameters if p.name == "format")
        assert format_param.required is False
        assert format_param.default == "markdown"

    def test_steps_parsed(self):
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        assert recipe.steps[0].step_type == StepType.COMMAND
        assert recipe.steps[1].step_type == StepType.PROMPT
        assert "${date}" in recipe.steps[0].content

    def test_discover_recipes(self):
        recipes = discover_recipes(FIXTURES)
        assert len(recipes) == 2
        names = {r.name for r in recipes}
        assert "daily-report" in names

    def test_discover_empty_dir(self, tmp_path):
        result = discover_recipes(tmp_path / "nonexistent")
        assert result == []

    def test_missing_name_raises(self, tmp_path):
        recipe_dir = tmp_path / "bad"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text("description: no name")
        with pytest.raises(RecipeParseError, match="missing 'name'"):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_invalid_yaml_raises(self, tmp_path):
        recipe_dir = tmp_path / "bad"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(": : invalid yaml [[")
        with pytest.raises(RecipeParseError):
            load_recipe(recipe_dir / "recipe.yaml")

    def test_default_on_error_policy_is_abort(self):
        """on_error 미지정 레시피는 ABORT 정책을 기본값으로 가진다."""
        recipe = load_recipe(FIXTURES / "daily-report" / "recipe.yaml")
        assert recipe.on_error == OnErrorPolicy.ABORT

    def test_on_error_and_rollback_parsed(self, tmp_path):
        recipe_dir = tmp_path / "with-policy"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: with-policy\n"
            "on_error: rollback\n"
            "steps:\n"
            "  - type: command\n"
            "    name: A\n"
            "    content: echo a\n"
            "    rollback: echo undo-a\n"
            "  - type: command\n"
            "    name: B\n"
            "    content: echo b\n"
            "    on_error: continue\n"
        )
        recipe = load_recipe(recipe_dir / "recipe.yaml")
        assert recipe.on_error == OnErrorPolicy.ROLLBACK
        assert recipe.steps[0].rollback == "echo undo-a"
        assert recipe.steps[0].on_error is None  # 레시피 기본값을 따름
        assert recipe.steps[1].on_error == OnErrorPolicy.CONTINUE

    def test_invalid_on_error_raises(self, tmp_path):
        recipe_dir = tmp_path / "bad-policy"
        recipe_dir.mkdir()
        (recipe_dir / "recipe.yaml").write_text(
            "name: bad-policy\non_error: maybe\nsteps: []\n"
        )
        with pytest.raises(RecipeParseError, match="Invalid on_error"):
            load_recipe(recipe_dir / "recipe.yaml")
