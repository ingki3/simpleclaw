"""운영자용 recipe.yaml 독립 검증 도구.

``recipe_validate``는 live recipe 디렉터리에 hot-load되는 YAML을 읽기 전용으로
검증한다. 운영자가 이름 또는 경로를 지정하면 configured recipe dir 기준으로만
대상을 resolve하고, 기존 loader와 동일한 parser를 호출한 뒤 empty/provided params
렌더 smoke와 slash command 충돌 경고를 JSON으로 반환한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simpleclaw.config import load_recipes_config
from simpleclaw.recipes.executor import _substitute_variables, render_instructions
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.recipes.models import RecipeDefinition, RecipeParseError

DEFAULT_CONFIG_PATH = Path("/Users/simplist/.simpleclaw/config.yaml")
_RESERVED_SLASH_COMMANDS = frozenset({"cron", "undo"})
_PREVIEW_LIMIT = 240


def handle_recipe_validate(
    args: dict[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``name`` 또는 ``path``와 선택적 ``render_params``를 담은 tool arguments.
        config_path: live config.yaml 경로. ``recipes.dir`` resolve 기준으로 쓴다.

    Returns:
        parse/discovery/render sanity 결과. 실패도 예외 대신 ``ok=false`` JSON으로
        반환해 tool-loop가 운영자에게 바로 원인을 전달할 수 있게 한다.
    """
    config = Path(config_path).expanduser()
    recipes_dir = Path(load_recipes_config(config)["dir"]).expanduser()
    payload: dict[str, Any] = {
        "ok": False,
        "read_only": True,
        "config_path": str(config),
        "recipes_dir": str(recipes_dir),
        "errors": [],
        "warnings": [],
    }

    recipe_path, resolve_error = _resolve_recipe_path(args, recipes_dir)
    if resolve_error is not None:
        payload["errors"].append(resolve_error)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    try:
        recipe = load_recipe(recipe_path)
    except RecipeParseError as exc:
        payload["recipe"] = {"path": str(recipe_path)}
        payload["errors"].append(str(exc))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    render_params = _normalize_render_params(args.get("render_params"))
    render = {
        "empty_params": _render_recipe(recipe, {}),
        "provided_params": _render_recipe(recipe, render_params),
    }
    warnings = _slash_collision_warnings(recipe)
    payload.update(
        {
            "ok": render["empty_params"]["ok"] and render["provided_params"]["ok"],
            "recipe": _recipe_summary(recipe, recipe_path),
            "render": render,
            "render_params_keys": sorted(render_params),
            "warnings": warnings,
        }
    )
    if not payload["ok"]:
        for key in ("empty_params", "provided_params"):
            error = render[key].get("error")
            if error:
                payload["errors"].append(f"{key}: {error}")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _resolve_recipe_path(args: dict[str, Any], recipes_dir: Path) -> tuple[Path, str | None]:
    """name/path 입력을 configured recipes dir 아래 recipe 파일로 resolve한다."""
    raw_path = args.get("path")
    raw_name = args.get("name")
    if raw_path:
        candidate = Path(str(raw_path)).expanduser()
        if candidate.is_dir():
            candidate = _recipe_file_in_dir(candidate)
        else:
            candidate = candidate.resolve()
        if not _is_within(candidate, recipes_dir.resolve()):
            return candidate, f"path must be under configured recipes dir: {recipes_dir}"
        if candidate.name not in {"recipe.yaml", "recipe.yml"}:
            return candidate, "path must point to recipe.yaml or recipe.yml"
        if not candidate.is_file():
            return candidate, f"recipe file not found: {candidate}"
        return candidate, None

    if raw_name:
        name = str(raw_name).strip().lstrip("/")
        if not name:
            return recipes_dir, "name must not be empty"
        candidate_dir = recipes_dir / name
        candidate = _recipe_file_in_dir(candidate_dir)
        if not candidate.is_file():
            return candidate, f"recipe not found for name '{name}': {candidate_dir}"
        return candidate, None

    return recipes_dir, "either 'name' or 'path' is required"


def _recipe_file_in_dir(directory: Path) -> Path:
    """디렉터리 안의 recipe.yaml/yml 후보를 반환한다."""
    expanded = directory.expanduser().resolve()
    yaml_path = expanded / "recipe.yaml"
    if yaml_path.is_file():
        return yaml_path
    return expanded / "recipe.yml"


def _is_within(path: Path, root: Path) -> bool:
    """``path``가 ``root`` 안에 있는지 부모 경계 기준으로 검사한다."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _normalize_render_params(raw: object) -> dict[str, str]:
    """render_params를 문자열 dict로 정규화한다."""
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _render_recipe(recipe: RecipeDefinition, params: dict[str, str]) -> dict[str, Any]:
    """instructions/steps 렌더 smoke를 수행하고 preview를 반환한다."""
    variables = _variables_with_defaults(recipe, params)
    try:
        if recipe.instructions:
            rendered = render_instructions(recipe.instructions, variables=variables)
            return _render_ok(rendered)
        rendered_steps = [
            _substitute_variables(str(step.content), variables)
            for step in recipe.steps
        ]
        return _render_ok("\n".join(rendered_steps))
    except Exception as exc:  # noqa: BLE001 — 진단 도구는 render 실패를 JSON화한다.
        return {"ok": False, "error": f"render failed: {exc}"}


def _variables_with_defaults(recipe: RecipeDefinition, params: dict[str, str]) -> dict[str, str]:
    """recipe parameter default와 요청 params를 합쳐 render 변수로 만든다."""
    variables: dict[str, str] = {}
    for param in recipe.parameters:
        if param.default:
            variables[param.name] = str(param.default)
    variables.update(params)
    return variables


def _render_ok(text: str) -> dict[str, Any]:
    """렌더 결과를 짧은 preview와 함께 성공 payload로 만든다."""
    return {
        "ok": True,
        "length": len(text),
        "preview": text[:_PREVIEW_LIMIT],
    }


def _recipe_summary(recipe: RecipeDefinition, path: Path) -> dict[str, Any]:
    """검증 대상 recipe의 주요 metadata를 요약한다."""
    return {
        "name": recipe.name,
        "description": recipe.description,
        "path": str(path),
        "recipe_dir": str(path.parent),
        "parameters": [
            {"name": p.name, "required": p.required, "has_default": bool(p.default)}
            for p in recipe.parameters
        ],
        "steps_count": len(recipe.steps),
        "has_instructions": bool(recipe.instructions),
    }


def _slash_collision_warnings(recipe: RecipeDefinition) -> list[str]:
    """내장 slash command와 recipe slash 이름이 충돌하면 warning을 반환한다."""
    if recipe.name not in _RESERVED_SLASH_COMMANDS:
        return []
    return [
        f"/{recipe.name} collides with built-in slash command and may not dispatch this recipe."
    ]


__all__ = ["DEFAULT_CONFIG_PATH", "handle_recipe_validate"]