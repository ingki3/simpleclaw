"""운영자용 recipe 생성/설치 도구.

``recipe_generate`` 는 일반 ``file_write`` 샌드박스를 넓히지 않고,
operator/development context에서만 recipe 초안 생성과 승인 설치를 수행한다.

설계 결정:
- v1은 ``instructions`` 기반 recipe만 생성한다. ``steps: command`` recipe는 실행
  가능한 셸 명령을 포함하므로 별도 allowlist/검토 흐름이 필요해 후속 범위로 둔다.
- draft는 workspace 아래에만 쓴다. live recipe 설치는 ``confirm=true``가 있을 때만
  configured ``recipes.dir`` 아래 고정 경로에 수행한다.
- 기존 recipe를 바꾸려면 ``overwrite=true``가 필요하며, 교체 전 timestamped backup을
  남긴다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from simpleclaw.agent.recipe_render import (
    render_instructions_preview,
    substitute_step_variables,
)
from simpleclaw.config import load_recipes_config
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.recipes.models import RecipeDefinition, RecipeParseError

DEFAULT_CONFIG_PATH = Path("/Users/simplist/.simpleclaw/config.yaml")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PREVIEW_LIMIT = 240
_RESERVED_SLASH_COMMANDS = frozenset({"cron", "undo"})


def handle_recipe_generate(
    args: dict[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    workspace_dir: str | Path,
) -> str:
    """``recipe_generate`` Function Calling handler.

    모든 결과는 JSON 문자열로 반환해 tool-loop가 예외 없이 운영자에게 원인을
    전달할 수 있게 한다.
    """
    config = Path(config_path).expanduser()
    workspace = Path(workspace_dir).expanduser()
    recipes_dir = Path(load_recipes_config(config)["dir"]).expanduser()
    action = str(args.get("action") or "").strip()
    name = str(args.get("name") or "").strip()
    draft_dir = workspace / "recipe_drafts" / name
    target_path = recipes_dir / name / "recipe.yaml"

    payload: dict[str, Any] = {
        "ok": False,
        "action": action,
        "installed": False,
        "config_path": str(config),
        "recipes_dir": str(recipes_dir),
        "workspace_dir": str(workspace),
        "draft_path": str(draft_dir / "recipe.yaml"),
        "target_path": str(target_path),
        "backup_path": None,
        "errors": [],
        "warnings": [],
    }

    if action not in {"draft", "install"}:
        payload["errors"].append("action must be 'draft' or 'install'")
        return _json(payload)

    validation = validate_recipe_candidate(
        args,
        candidate_dir=draft_dir,
        render_params=_normalize_render_params(args.get("render_params")),
    )
    payload["validation"] = validation
    payload["warnings"].extend(validation.get("warnings", []))
    if not validation["ok"]:
        payload["errors"].extend(validation.get("errors", []))
        return _json(payload)

    if action == "draft":
        payload.update({"ok": True, "recipe": validation.get("recipe")})
        return _json(payload)

    if not bool(args.get("confirm", False)):
        payload["errors"].append("install requires confirm=true")
        return _json(payload)
    if target_path.exists() and not bool(args.get("overwrite", False)):
        payload["errors"].append(
            "target recipe already exists; pass overwrite=true to backup and replace"
        )
        return _json(payload)

    backup_path: Path | None = None
    if target_path.exists():
        backup_path = target_path.with_name(f"recipe.yaml.bak-{_timestamp()}")
        backup_path.write_text(target_path.read_text(encoding="utf-8"), encoding="utf-8")

    _atomic_write(target_path, build_recipe_yaml(args))

    try:
        installed_recipe = load_recipe(target_path)
    except RecipeParseError as exc:
        payload["errors"].append(f"post-install validation failed: {exc}")
        return _json(payload)

    payload.update(
        {
            "ok": True,
            "installed": True,
            "backup_path": str(backup_path) if backup_path else None,
            "recipe": _recipe_summary(installed_recipe, target_path),
        }
    )
    return _json(payload)


def build_recipe_yaml(args: dict[str, Any]) -> str:
    """Function-call args에서 instructions 기반 ``recipe.yaml`` 텍스트를 만든다."""
    name = str(args.get("name") or "").strip()
    recipe: dict[str, Any] = {
        "name": name,
        "description": str(args.get("description") or ""),
    }
    trigger = str(args.get("trigger") or "").strip()
    if trigger:
        recipe["trigger"] = trigger

    parameters = _normalize_parameters(args.get("parameters"))
    if parameters:
        recipe["parameters"] = parameters

    skills = _normalize_string_list(args.get("skills"))
    if skills:
        recipe["skills"] = skills

    recipe["instructions"] = str(args.get("instructions") or "").rstrip() + "\n"
    return yaml.safe_dump(recipe, allow_unicode=True, sort_keys=False)


def validate_recipe_candidate(
    args: dict[str, Any],
    *,
    candidate_dir: Path,
    render_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """candidate recipe를 임시 경로에 써서 loader/render smoke를 수행한다."""
    candidate_path = candidate_dir / "recipe.yaml"
    errors = _static_candidate_errors(args)
    warnings: list[str] = []
    if errors:
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "candidate_path": str(candidate_path),
        }

    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(build_recipe_yaml(args), encoding="utf-8")
    try:
        recipe = load_recipe(candidate_path)
    except RecipeParseError as exc:
        return {
            "ok": False,
            "errors": [str(exc)],
            "warnings": warnings,
            "candidate_path": str(candidate_path),
        }

    render = {
        "empty_params": _render_recipe(recipe, {}),
        "provided_params": _render_recipe(recipe, _normalize_render_params(render_params)),
    }
    if recipe.name in _RESERVED_SLASH_COMMANDS:
        warnings.append(
            f"/{recipe.name} collides with built-in slash command and may not dispatch "
            "this recipe."
        )

    ok = render["empty_params"]["ok"] and render["provided_params"]["ok"]
    render_errors: list[str] = []
    if not ok:
        for key in ("empty_params", "provided_params"):
            error = render[key].get("error")
            if error:
                render_errors.append(f"{key}: {error}")

    return {
        "ok": ok,
        "errors": render_errors,
        "warnings": warnings,
        "candidate_path": str(candidate_path),
        "recipe": _recipe_summary(recipe, candidate_path),
        "render": render,
    }


def _static_candidate_errors(args: dict[str, Any]) -> list[str]:
    """파일 쓰기 전 검출 가능한 recipe candidate 오류를 반환한다."""
    errors: list[str] = []
    name = str(args.get("name") or "").strip()
    instructions = str(args.get("instructions") or "")
    if not _NAME_RE.fullmatch(name):
        errors.append(
            "name must match ^[a-z0-9][a-z0-9_-]{0,63}$ and contain no path "
            "separators"
        )
    if not instructions.strip():
        errors.append("instructions must be a non-empty string")
    return errors


def _normalize_string_list(raw: object) -> list[str]:
    """문자열 list 입력만 정규화한다."""
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _normalize_parameters(raw: object) -> list[dict[str, Any]]:
    """recipe parameter 입력을 loader가 이해하는 YAML shape로 정규화한다."""
    if not isinstance(raw, list):
        return []
    parameters: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parameter: dict[str, Any] = {
            "name": name,
            "description": str(item.get("description") or ""),
            "required": bool(item.get("required", True)),
        }
        if "default" in item:
            parameter["default"] = str(item.get("default") or "")
        parameters.append(parameter)
    return parameters


def _normalize_render_params(raw: object) -> dict[str, str]:
    """``render_params``를 문자열 dict로 정규화한다."""
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _render_recipe(recipe: RecipeDefinition, params: dict[str, str]) -> dict[str, Any]:
    """instructions/steps 렌더 smoke를 수행하고 preview를 반환한다."""
    variables: dict[str, str] = {}
    for param in recipe.parameters:
        if param.default:
            variables[param.name] = str(param.default)
    variables.update(params)
    try:
        if recipe.instructions:
            rendered = render_instructions_preview(recipe.instructions, variables=variables)
        else:
            rendered = "\n".join(
                substitute_step_variables(str(step.content), variables)
                for step in recipe.steps
            )
        return {"ok": True, "length": len(rendered), "preview": rendered[:_PREVIEW_LIMIT]}
    except Exception as exc:  # noqa: BLE001 — 진단 도구는 render 실패를 JSON화한다.
        return {"ok": False, "error": f"render failed: {exc}"}


def _recipe_summary(recipe: RecipeDefinition, path: Path) -> dict[str, Any]:
    """recipe metadata를 JSON-safe dict로 요약한다."""
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


def _timestamp() -> str:
    """백업 파일명용 timestamp."""
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _atomic_write(path: Path, content: str) -> None:
    """같은 디렉터리의 임시 파일을 거쳐 atomic replace한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _json(payload: dict[str, Any]) -> str:
    """Tool result JSON을 안정적인 key order로 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


__all__ = ["DEFAULT_CONFIG_PATH", "build_recipe_yaml", "handle_recipe_generate", "validate_recipe_candidate"]
