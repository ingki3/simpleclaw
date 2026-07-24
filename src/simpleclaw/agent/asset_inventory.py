"""운영자용 런타임 asset inventory 도구.

``asset_inventory``는 운영자가 native tools, SimpleClaw runtime skills,
recipes, MCP 설정/연결 상태, selector 설정을 한 JSON에서 구분해 확인하기 위한
read-only 진단 도구다. Hermes 프로필 skill과 런타임 skill을 혼동하지 않도록
응답의 skill 항목에는 항상 ``source=simpleclaw_runtime_skill``을 포함한다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from simpleclaw.agent.tool_schemas import (
    ToolScope,
    build_native_tool_registry,
    native_tool_names,
)
from simpleclaw.config import load_asset_selection_config, load_recipes_config
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.recipes.models import RecipeDefinition, RecipeParseError
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.mcp_client import MCPManager
from simpleclaw.skills.models import SkillDefinition

DEFAULT_CONFIG_PATH = Path("/Users/simplist/.simpleclaw/config.yaml")

_ALLOWED_TYPES = frozenset({"all", "native_tools", "skills", "recipes", "mcp", "selector"})
_DEFAULT_SKILLS_CONFIG = {
    "local_dir": ".agent/skills",
    "global_dir": "~/.agents/skills",
}


def handle_asset_inventory(
    args: dict[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    skills: Iterable[SkillDefinition] | None = None,
    recipes: Iterable[RecipeDefinition] | None = None,
    mcp_manager: MCPManager | None = None,
) -> str:
    """Function Calling 핸들러용 JSON 문자열을 반환한다.

    Args:
        args: ``type``/``include_paths``/``include_errors`` 옵션을 담은 tool arguments.
        config_path: live ``config.yaml`` 경로. 기본값은 운영 기본 경로다.
        skills: 이미 hot-reload된 runtime skill 목록. 없으면 config 경로로 재탐색한다.
        recipes: 이미 hot-reload된 recipe 목록. 없으면 recipe directory를 스캔한다.
        mcp_manager: 현재 연결된 MCP manager. 없으면 config 요약만 반환한다.

    Returns:
        요청한 asset 섹션을 담은 read-only JSON 문자열. config/asset 파싱 실패는
        예외 대신 ``errors`` 필드로 축약한다.
    """
    path = Path(config_path).expanduser()
    inventory_type = _normalize_type(args.get("type"))
    include_paths = bool(args.get("include_paths", False))
    include_errors = bool(args.get("include_errors", False))
    raw_config, config_error = _read_config(path)
    errors: list[str] = []

    sections: dict[str, Any] = {}
    requested = _requested_sections(inventory_type)
    if config_error and set(requested) != {"native_tools"}:
        errors.append(config_error)
    if "native_tools" in requested:
        sections["native_tools"] = _native_tool_inventory()
    if "skills" in requested:
        sections["skills"] = _skill_inventory(
            skills,
            raw_config=raw_config,
            include_paths=include_paths,
            errors=errors,
        )
    if "recipes" in requested:
        sections["recipes"] = _recipe_inventory(
            recipes,
            config_path=path,
            include_paths=include_paths,
            include_errors=include_errors,
            errors=errors,
        )
    if "mcp" in requested:
        sections["mcp"] = _mcp_inventory(
            raw_config,
            mcp_manager=mcp_manager,
            include_paths=include_paths,
        )
    if "selector" in requested:
        sections["selector"] = {
            "config": load_asset_selection_config(path),
        }

    payload: dict[str, Any] = {
        "ok": not errors,
        "read_only": True,
        "type": inventory_type,
        "include_paths": include_paths,
        "include_errors": include_errors,
        "config_path": str(path),
        "sections": sections,
    }
    if include_errors and errors:
        payload["errors"] = errors
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _normalize_type(raw: object) -> str:
    """허용 type 외 입력은 all로 fail-closed 정규화한다."""
    value = str(raw or "all")
    return value if value in _ALLOWED_TYPES else "all"


def _requested_sections(inventory_type: str) -> tuple[str, ...]:
    """type 요청을 실제 payload section 목록으로 변환한다."""
    if inventory_type == "all":
        return ("native_tools", "skills", "recipes", "mcp", "selector")
    return (inventory_type,)


def _read_config(path: Path) -> tuple[dict[str, Any], str | None]:
    """config.yaml을 읽고 실패 시 빈 dict와 오류 문자열을 반환한다."""
    if not path.is_file():
        return {}, f"config file not found: {path}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {}, f"config load failed: {exc}"
    if not isinstance(data, dict):
        return {}, "config root is not a mapping"
    return data, None


def _native_tool_inventory() -> list[dict[str, Any]]:
    """registry의 native tool metadata와 operator context 노출 여부를 요약한다."""
    visible_names = native_tool_names(
        cron_available=True,
        scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
        operator_gate=True,
    )
    return [
        {
            "name": spec.definition.name,
            "scope": spec.scope.value,
            "risk": spec.risk.value,
            "operator_gate_required": spec.operator_gate_required,
            "enabled": spec.definition.name in visible_names,
            "aliases": list(spec.aliases),
        }
        for spec in build_native_tool_registry(
            cron_available=True,
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
            operator_gate=True,
        )
    ]


def _skill_inventory(
    skills: Iterable[SkillDefinition] | None,
    *,
    raw_config: dict[str, Any],
    include_paths: bool,
    errors: list[str],
) -> list[dict[str, Any]]:
    """runtime skill 목록에 source dir, script path, executable 상태를 붙인다."""
    skills_config = _skills_config(raw_config)
    source_dirs = {
        "local": str(Path(skills_config["local_dir"]).expanduser()),
        "global": str(Path(skills_config["global_dir"]).expanduser()),
    }
    if skills is None:
        try:
            skills = discover_skills(source_dirs["local"], source_dirs["global"])
        except Exception as exc:  # noqa: BLE001 — inventory는 진단 도구라 실패를 JSON화한다.
            errors.append(f"skill discovery failed: {exc}")
            skills = []

    items: list[dict[str, Any]] = []
    for skill in sorted(skills, key=lambda item: item.name):
        scope = skill.scope.value if hasattr(skill.scope, "value") else str(skill.scope)
        item: dict[str, Any] = {
            "name": skill.name,
            "description": skill.description,
            "source": "simpleclaw_runtime_skill",
            "scope": scope,
            "source_dir": source_dirs.get(scope, ""),
            "commands_count": len(skill.commands or []),
            "has_retry_policy": skill.retry_policy is not None,
        }
        script_path = str(Path(skill.script_path).expanduser()) if skill.script_path else ""
        item["script_path"] = script_path
        item["executable"] = bool(script_path and os.access(script_path, os.X_OK))
        if include_paths:
            item["skill_dir"] = str(Path(skill.skill_dir).expanduser()) if skill.skill_dir else ""
        items.append(item)
    return items


def _skills_config(raw_config: dict[str, Any]) -> dict[str, str]:
    """raw config에서 skills directory 설정을 기본값으로 보강한다."""
    configured = raw_config.get("skills", {})
    if not isinstance(configured, dict):
        configured = {}
    merged = deepcopy(_DEFAULT_SKILLS_CONFIG)
    merged.update({key: configured.get(key, default) for key, default in merged.items()})
    return merged


def _recipe_inventory(
    recipes: Iterable[RecipeDefinition] | None,
    *,
    config_path: Path,
    include_paths: bool,
    include_errors: bool,
    errors: list[str],
) -> list[dict[str, Any]]:
    """recipe directory를 스캔해 parse status와 캐시된 recipe metadata를 요약한다."""
    recipes_config = load_recipes_config(config_path)
    recipes_dir = Path(recipes_config["dir"]).expanduser()
    cached_by_dir = {
        str(Path(recipe.recipe_dir).expanduser()): recipe
        for recipe in (recipes or [])
        if recipe.recipe_dir
    }
    if recipes is None and recipes_dir.is_dir():
        # recipes 인자를 받지 못한 standalone 호출에서도 유효 recipe metadata를 제공한다.
        for recipe_file in _iter_recipe_files(recipes_dir):
            try:
                recipe = load_recipe(recipe_file)
                cached_by_dir[str(recipe_file.parent)] = recipe
            except RecipeParseError:
                continue

    items: list[dict[str, Any]] = []
    if not recipes_dir.is_dir():
        errors.append(f"recipes directory not found: {recipes_dir}")
        return items

    for recipe_file in _iter_recipe_files(recipes_dir):
        recipe = cached_by_dir.get(str(recipe_file.parent))
        item: dict[str, Any] = {
            "directory": recipe_file.parent.name,
            "parse_status": "ok",
            "name": recipe.name if recipe else recipe_file.parent.name,
        }
        if include_paths:
            item["path"] = str(recipe_file)
            item["recipe_dir"] = str(recipe_file.parent)
        try:
            parsed = recipe or load_recipe(recipe_file)
            item.update({
                "description": parsed.description,
                "parameters_count": len(parsed.parameters or []),
                "steps_count": len(parsed.steps or []),
                "has_instructions": bool(parsed.instructions),
            })
        except RecipeParseError as exc:
            item["parse_status"] = "error"
            item["description"] = ""
            item["parameters_count"] = 0
            item["steps_count"] = 0
            item["has_instructions"] = False
            if include_errors:
                item["error"] = str(exc)
        items.append(item)
    return items


def _iter_recipe_files(recipes_dir: Path) -> list[Path]:
    """recipes_dir 하위의 recipe.yaml/yml 파일을 안정적인 순서로 반환한다."""
    files: list[Path] = []
    for entry in sorted(recipes_dir.iterdir()):
        if not entry.is_dir():
            continue
        recipe_file = entry / "recipe.yaml"
        if not recipe_file.is_file():
            recipe_file = entry / "recipe.yml"
        if recipe_file.is_file():
            files.append(recipe_file)
    return files


def _mcp_inventory(
    raw_config: dict[str, Any],
    *,
    mcp_manager: MCPManager | None,
    include_paths: bool,
) -> dict[str, Any]:
    """MCP config와 현재 manager의 연결/도구 상태를 요약한다."""
    mcp_config = raw_config.get("mcp", {})
    if not isinstance(mcp_config, dict):
        mcp_config = {}
    servers = mcp_config.get("servers", {})
    if not isinstance(servers, dict):
        servers = {}
    configured = sorted(str(name) for name in servers)
    connected = sorted(mcp_manager.get_connected_servers()) if mcp_manager else []
    connection_errors: dict[str, str] = {}
    if mcp_manager is not None and hasattr(mcp_manager, "get_connection_errors"):
        connection_errors = dict(mcp_manager.get_connection_errors())
    tools = []
    if mcp_manager is not None:
        for tool in sorted(
            mcp_manager.list_tools(), key=lambda item: (item.source_name, item.name)
        ):
            metadata = getattr(tool, "metadata", None)
            if not isinstance(metadata, dict):
                metadata = {}
            input_schema = metadata.get("input_schema")
            item: dict[str, Any] = {
                "name": tool.name,
                "source_name": tool.source_name,
                "scope": str(metadata.get("scope") or "operator"),
                "has_input_schema": bool(input_schema),
            }
            # 전체 schema는 요청 시에만 — 기본 응답의 토큰 팽창을 막는다.
            if include_paths and input_schema:
                item["input_schema"] = input_schema
            tools.append(item)
    payload: dict[str, Any] = {
        "configured_servers": configured,
        "connected_servers": connected,
        "connection_errors": connection_errors,
        "tools": tools,
    }
    if include_paths:
        payload["server_configs"] = {
            str(name): _summarize_mcp_server(config)
            for name, config in sorted(servers.items())
            if isinstance(config, dict)
        }
    return payload


def _summarize_mcp_server(config: dict[str, Any]) -> dict[str, Any]:
    """MCP 서버 설정에서 시크릿성 env 값은 제외하고 실행 경로만 요약한다."""
    return {
        "command": config.get("command", ""),
        "args_count": len(config.get("args", []) or []),
        "env_keys": sorted((config.get("env") or {}).keys()) if isinstance(config.get("env"), dict) else [],
    }


__all__ = ["DEFAULT_CONFIG_PATH", "handle_asset_inventory"]