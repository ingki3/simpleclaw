"""레시피 탐색 및 YAML 파싱 모듈.

레시피 디렉터리를 스캔하여 recipe.yaml(또는 .yml) 파일을 찾아
RecipeDefinition으로 변환한다.

동작 흐름:
1. 지정된 디렉터리의 하위 폴더를 순회
2. 각 폴더에서 recipe.yaml 또는 recipe.yml 파일을 탐색
3. YAML을 파싱하여 파라미터, 스텝, 지시문을 추출

설계 결정:
- 파싱 실패 시 해당 레시피만 건너뛰고 경고 로그 출력
- recipe.yaml과 recipe.yml 모두 지원하여 유연성 확보
"""

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
    """지정된 디렉터리에서 모든 레시피를 탐색한다.

    Args:
        recipes_dir: 레시피가 위치한 상위 디렉터리 경로

    Returns:
        파싱된 RecipeDefinition 목록 (파싱 실패한 레시피는 제외)
    """
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
    """단일 recipe.yaml 파일을 로드하고 파싱한다.

    Args:
        recipe_path: recipe.yaml 파일의 경로

    Returns:
        파싱된 RecipeDefinition

    Raises:
        RecipeParseError: YAML 파싱 실패 또는 필수 필드 누락 시
    """
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

    # 파라미터 파싱
    parameters = []
    for pdata in data.get("parameters", []):
        if isinstance(pdata, dict):
            parameters.append(RecipeParameter(
                name=pdata.get("name", ""),
                description=pdata.get("description", ""),
                required=pdata.get("required", True),
                default=str(pdata.get("default", "")),
            ))

    # 스텝 파싱
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
