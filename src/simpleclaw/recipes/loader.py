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
    OnErrorPolicy,
    RecipeDefinition,
    RecipeParameter,
    RecipeParseError,
    RecipeStep,
    StepType,
)

logger = logging.getLogger(__name__)


def _parse_on_error(value: object, source: Path) -> OnErrorPolicy | None:
    """``on_error`` 문자열을 ``OnErrorPolicy`` 로 변환한다.

    None/빈 값이면 None을 반환해 호출자가 기본값 결정 책임을 갖게 한다.
    잘못된 값은 ``RecipeParseError`` 로 즉시 실패시킨다(YAML 오타가 정책처럼
    런타임에서 무시되어 디버깅이 어려운 상황을 방지).
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RecipeParseError(
            f"'on_error' must be a string in {source}, got {type(value).__name__}"
        )
    try:
        return OnErrorPolicy(value)
    except ValueError as e:
        valid = ", ".join(p.value for p in OnErrorPolicy)
        raise RecipeParseError(
            f"Invalid on_error '{value}' in {source} (expected one of: {valid})"
        ) from e


def _scan_recipes_dir(recipes_path: Path) -> list[RecipeDefinition]:
    """단일 디렉터리만 스캔해 ``RecipeDefinition`` 리스트를 만든다.

    공유 헬퍼 — ``discover_recipes`` 가 primary/legacy 경로를 합치는 데 사용한다.
    파싱 실패는 경고 로그를 남기고 건너뛴다.
    """
    if not recipes_path.is_dir():
        return []

    recipes: list[RecipeDefinition] = []
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


def discover_recipes(
    recipes_dir: str | Path,
    legacy_dir: str | Path | None = None,
) -> list[RecipeDefinition]:
    """지정된 디렉터리에서 모든 레시피를 탐색한다.

    Args:
        recipes_dir: 레시피가 위치한 상위 디렉터리 경로 (1차 위치).
        legacy_dir: BIZ-202 이전 위치 (``.agent/recipes``) 같은 폴백 디렉터리.
            존재하고 primary 에는 없는 이름만 합쳐 반환하며 deprecation 경고를
            한 번 남긴다. ``None`` 이면 폴백 없이 primary 만 본다.

    Returns:
        파싱된 RecipeDefinition 목록 (파싱 실패한 레시피는 제외).
        같은 ``name`` 이 primary 와 legacy 양쪽에 있으면 **primary 우선**.
    """
    recipes_path = Path(recipes_dir).expanduser()
    primary = _scan_recipes_dir(recipes_path)
    if not primary:
        logger.debug("Recipes directory empty or missing: %s", recipes_path)

    if legacy_dir is None:
        return primary

    legacy_path = Path(legacy_dir).expanduser()
    if legacy_path.resolve() == recipes_path.resolve():
        # 운영자가 primary 와 legacy 를 같은 경로로 지정 — 중복 스캔 의미 없음.
        return primary

    legacy_recipes = _scan_recipes_dir(legacy_path)
    if not legacy_recipes:
        return primary

    # primary 에 이미 있는 이름은 legacy 가 가리지 않게 한다 — 마이그레이션 도중
    # 양쪽에 같은 레시피가 잠시 공존하더라도 사용자가 갱신한 primary 가 우선.
    primary_names = {r.name for r in primary}
    legacy_only = [r for r in legacy_recipes if r.name not in primary_names]
    if legacy_only:
        logger.warning(
            "DEPRECATED recipes directory: %d recipe(s) loaded from legacy '%s' "
            "(names: %s). Move them under '%s' — the legacy fallback is scheduled "
            "for removal in the next minor release (BIZ-202).",
            len(legacy_only),
            legacy_path,
            ", ".join(sorted(r.name for r in legacy_only)),
            recipes_path,
        )
    return primary + legacy_only


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
                on_error=_parse_on_error(sdata.get("on_error"), recipe_path),
                rollback=str(sdata.get("rollback") or ""),
            ))

    # 레시피 단위 기본 실패 정책. 미지정 시 기존 동작과 동일한 ABORT.
    default_on_error = (
        _parse_on_error(data.get("on_error"), recipe_path) or OnErrorPolicy.ABORT
    )

    return RecipeDefinition(
        name=name,
        description=data.get("description", ""),
        parameters=parameters,
        steps=steps,
        instructions=data.get("instructions", ""),
        recipe_dir=str(recipe_path.parent),
        on_error=default_on_error,
    )
