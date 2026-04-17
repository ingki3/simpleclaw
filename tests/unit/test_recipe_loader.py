"""Tests for recipe loader."""

from pathlib import Path

import pytest

from simpleclaw.recipes.loader import discover_recipes, load_recipe
from simpleclaw.recipes.models import RecipeParseError, StepType

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
        assert len(recipes) == 1
        assert recipes[0].name == "daily-report"

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
