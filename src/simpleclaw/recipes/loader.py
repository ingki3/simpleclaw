"""Recipe discovery and YAML parsing."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from simpleclaw.recipes.models import (
    RecipeDefinition,
    RecipeParameter,
    RecipeParseError,
    RecipeStep,
    StepType,
)

logger = logging.getLogger(__name__)


def discover_recipes(recipes_dir: str | Path) -> list[RecipeDefinition]:
    """Discover all recipes in the given directory."""
    recipes_path = Path(recipes_dir).expanduser()
    if not recipes_path.is_dir():
        logger.debug("Recipes directory does not exist: %s", recipes_path)
        return []

    recipes = []
    for entry in sorted(recipes_path.iterdir()):
        if not entry.is_dir():
            continue
        recipe_file = entry / "recipe.yaml"
        if not recipe_file.is_file():
            recipe_file = entry / "recipe.yml"
        if not recipe_file.is_file():
            continue

        try:
            recipe = load_recipe(recipe_file)
            recipes.append(recipe)
        except RecipeParseError as e:
            logger.warning("Skipping invalid recipe %s: %s", entry.name, e)

    return recipes


def load_recipe(recipe_path: str | Path) -> RecipeDefinition:
    """Load and parse a single recipe.yaml file."""
    recipe_path = Path(recipe_path)

    try:
        with open(recipe_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        raise RecipeParseError(f"Failed to parse {recipe_path}: {e}") from e

    if not isinstance(data, dict):
        raise RecipeParseError(f"Invalid recipe format in {recipe_path}")

    name = data.get("name")
    if not name:
        raise RecipeParseError(f"Recipe missing 'name' field in {recipe_path}")

    # Parse parameters
    parameters = []
    for pdata in data.get("parameters", []):
        if isinstance(pdata, dict):
            parameters.append(RecipeParameter(
                name=pdata.get("name", ""),
                description=pdata.get("description", ""),
                required=pdata.get("required", True),
                default=str(pdata.get("default", "")),
            ))

    # Parse steps
    steps = []
    for sdata in data.get("steps", []):
        if isinstance(sdata, dict):
            try:
                step_type = StepType(sdata.get("type", "prompt"))
            except ValueError:
                raise RecipeParseError(
                    f"Invalid step type '{sdata.get('type')}' in {recipe_path}"
                )
            steps.append(RecipeStep(
                step_type=step_type,
                name=sdata.get("name", ""),
                content=sdata.get("content", ""),
            ))

    return RecipeDefinition(
        name=name,
        description=data.get("description", ""),
        parameters=parameters,
        steps=steps,
        instructions=data.get("instructions", ""),
        recipe_dir=str(recipe_path.parent),
    )
