"""recipe_generate 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.recipe_generate import (
    build_recipe_yaml,
    handle_recipe_generate,
    install_validated_recipe,
    static_candidate_errors,
    validate_recipe_candidate,
)
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall
from simpleclaw.recipes.loader import discover_recipes


def _write_config(tmp_path, recipes_dir):
    """테스트용 config.yaml을 작성한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"recipes:\n  dir: {recipes_dir}\n", encoding="utf-8")
    return config


def test_recipe_generate_is_not_exposed_to_runtime_context():
    """기본 runtime build에는 보이지 않고 operator/development gate가 열릴 때만 노출된다."""
    runtime_names = {tool.name for tool in build_tool_definitions(skills=[])}
    operator_names = {
        tool.name
        for tool in build_tool_definitions(
            skills=[],
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
            operator_gate=True,
        )
    }

    assert "recipe_generate" not in runtime_names
    assert "recipe_generate" in operator_names


def test_build_recipe_yaml_renders_instructions_recipe():
    """structured args를 instructions 기반 recipe.yaml로 직렬화한다."""
    text = build_recipe_yaml(
        {
            "name": "lotte-live",
            "description": "롯데 야구 실시간 경기 확인",
            "trigger": "롯데 야구, 롯데 경기",
            "skills": ["web-search-skill"],
            "parameters": [
                {
                    "name": "query",
                    "description": "검색 질의어",
                    "required": False,
                    "default": "롯데 자이언츠 실시간 스코어",
                }
            ],
            "instructions": "{{ query }}로 실시간 경기 상황을 확인해줘.",
        }
    )
    data = yaml.safe_load(text)

    assert data["name"] == "lotte-live"
    assert data["description"] == "롯데 야구 실시간 경기 확인"
    assert data["trigger"] == "롯데 야구, 롯데 경기"
    assert data["skills"] == ["web-search-skill"]
    assert data["parameters"][0]["name"] == "query"
    assert data["parameters"][0]["required"] is False
    assert "{{ query }}" in data["instructions"]


def test_validate_recipe_candidate_rejects_path_traversal_name(tmp_path):
    """recipe name은 path separator를 포함할 수 없다."""
    payload = validate_recipe_candidate(
        {"name": "../evil", "instructions": "do something"},
        candidate_dir=tmp_path / "draft",
        render_params={},
    )

    assert payload["ok"] is False
    assert any("name" in error for error in payload["errors"])


def test_validate_recipe_candidate_rejects_empty_instructions(tmp_path):
    """v1 recipe_generate는 non-empty instructions를 요구한다."""
    payload = validate_recipe_candidate(
        {"name": "empty", "instructions": "   "},
        candidate_dir=tmp_path / "draft",
        render_params={},
    )

    assert payload["ok"] is False
    assert any("instructions" in error for error in payload["errors"])


def test_validate_recipe_candidate_loads_and_renders(tmp_path):
    """candidate recipe는 기존 loader와 render smoke를 통과해야 한다."""
    payload = validate_recipe_candidate(
        {
            "name": "ok-recipe",
            "instructions": "Report {{ query }} on {{ today }}",
            "parameters": [{"name": "query", "required": False, "default": "롯데"}],
        },
        candidate_dir=tmp_path / "draft",
        render_params={"query": "두산"},
    )

    assert payload["ok"] is True
    assert payload["recipe"]["name"] == "ok-recipe"
    assert "두산" in payload["render"]["provided_params"]["preview"]


def test_recipe_generate_draft_writes_only_workspace_draft(tmp_path):
    """draft는 workspace 아래 recipe_drafts에만 쓰고 recipes.dir에는 설치하지 않는다."""
    recipes_dir = tmp_path / "recipes"
    workspace_dir = tmp_path / "workspace"
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(
        handle_recipe_generate(
            {
                "action": "draft",
                "name": "lotte-live",
                "description": "롯데 야구 실시간",
                "instructions": "{{ query }}로 실시간 스코어를 확인해줘.",
                "parameters": [
                    {"name": "query", "required": False, "default": "롯데 야구"}
                ],
                "render_params": {"query": "롯데 두산"},
            },
            config_path=config,
            workspace_dir=workspace_dir,
        )
    )

    draft_path = workspace_dir / "recipe_drafts" / "lotte-live" / "recipe.yaml"
    target_path = recipes_dir / "lotte-live" / "recipe.yaml"
    assert payload["ok"] is True
    assert payload["installed"] is False
    assert payload["draft_path"] == str(draft_path)
    assert draft_path.is_file()
    assert not target_path.exists()


def test_recipe_generate_install_requires_confirm(tmp_path):
    """install은 confirm=true 없이는 recipes.dir에 쓰지 않는다."""
    recipes_dir = tmp_path / "recipes"
    workspace_dir = tmp_path / "workspace"
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(
        handle_recipe_generate(
            {"action": "install", "name": "lotte-live", "instructions": "확인해줘."},
            config_path=config,
            workspace_dir=workspace_dir,
        )
    )

    assert payload["ok"] is False
    assert any("confirm=true" in error for error in payload["errors"])
    assert not (recipes_dir / "lotte-live" / "recipe.yaml").exists()


def test_recipe_generate_install_writes_to_recipes_dir_when_confirmed(tmp_path):
    """confirm된 install은 configured recipes.dir 아래 고정 경로에 recipe를 쓴다."""
    recipes_dir = tmp_path / "recipes"
    workspace_dir = tmp_path / "workspace"
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(
        handle_recipe_generate(
            {
                "action": "install",
                "name": "lotte-live",
                "description": "롯데 야구 실시간",
                "instructions": "롯데 경기 상황을 확인해줘.",
                "confirm": True,
            },
            config_path=config,
            workspace_dir=workspace_dir,
        )
    )

    target = recipes_dir / "lotte-live" / "recipe.yaml"
    assert payload["ok"] is True
    assert payload["installed"] is True
    assert payload["target_path"] == str(target)
    assert target.is_file()
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["name"] == "lotte-live"


def test_recipe_generate_install_existing_requires_overwrite_and_creates_backup(tmp_path):
    """기존 recipe 교체는 overwrite=true와 timestamp backup을 요구한다."""
    recipes_dir = tmp_path / "recipes"
    workspace_dir = tmp_path / "workspace"
    target_dir = recipes_dir / "lotte-live"
    target_dir.mkdir(parents=True)
    existing = target_dir / "recipe.yaml"
    existing.write_text("name: lotte-live\ninstructions: old\n", encoding="utf-8")
    config = _write_config(tmp_path, recipes_dir)

    blocked = json.loads(
        handle_recipe_generate(
            {
                "action": "install",
                "name": "lotte-live",
                "instructions": "new",
                "confirm": True,
            },
            config_path=config,
            workspace_dir=workspace_dir,
        )
    )
    assert blocked["ok"] is False
    assert any("overwrite=true" in error for error in blocked["errors"])

    installed = json.loads(
        handle_recipe_generate(
            {
                "action": "install",
                "name": "lotte-live",
                "instructions": "new",
                "confirm": True,
                "overwrite": True,
            },
            config_path=config,
            workspace_dir=workspace_dir,
        )
    )

    assert installed["ok"] is True
    assert installed["backup_path"]
    assert Path(installed["backup_path"]).is_file()
    assert "old" in Path(installed["backup_path"]).read_text(encoding="utf-8")
    assert "new" in existing.read_text(encoding="utf-8")


def test_recipe_generate_install_is_discoverable(tmp_path):
    """설치된 recipe는 runtime discovery에서 발견된다."""
    recipes_dir = tmp_path / "recipes"
    workspace_dir = tmp_path / "workspace"
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(
        handle_recipe_generate(
            {
                "action": "install",
                "name": "lotte-live",
                "description": "롯데 야구 실시간",
                "instructions": "롯데 경기 상황을 확인해줘.",
                "confirm": True,
            },
            config_path=config,
            workspace_dir=workspace_dir,
        )
    )

    recipes = discover_recipes(recipes_dir)
    assert payload["ok"] is True
    assert [recipe.name for recipe in recipes] == ["lotte-live"]


def test_install_validated_recipe_helper_enforces_confirm_and_backup(tmp_path):
    """BIZ-428 — recipe_learning materialize가 재사용하는 공용 install policy 검증."""
    target = tmp_path / "recipes" / "demo" / "recipe.yaml"
    args = {"name": "demo", "instructions": "확인해줘."}

    blocked = install_validated_recipe(args, target_path=target)
    assert blocked["ok"] is False
    assert any("confirm=true" in error for error in blocked["errors"])
    assert not target.exists()

    installed = install_validated_recipe({**args, "confirm": True}, target_path=target)
    assert installed["ok"] is True
    assert installed["installed"] is True
    assert target.is_file()

    collision = install_validated_recipe({**args, "confirm": True}, target_path=target)
    assert collision["ok"] is False
    assert any("overwrite=true" in error for error in collision["errors"])

    replaced = install_validated_recipe(
        {**args, "confirm": True, "overwrite": True}, target_path=target
    )
    assert replaced["ok"] is True
    assert replaced["backup_path"]
    assert Path(replaced["backup_path"]).is_file()


def test_static_candidate_errors_is_reusable_public_helper():
    """recipe_learning 후보 검증에서 재사용하는 정적 오류 helper 회귀."""
    assert static_candidate_errors({"name": "ok-recipe", "instructions": "do"}) == []
    assert any(
        "name" in error
        for error in static_candidate_errors({"name": "../evil", "instructions": "do"})
    )
    assert any(
        "instructions" in error
        for error in static_candidate_errors({"name": "ok", "instructions": " "})
    )


@pytest.mark.asyncio
async def test_orchestrator_recipe_generate_dispatch_requires_operator_context(
    tmp_path, monkeypatch
):
    """수동 dispatch도 operator context가 아니면 recipe_generate를 실행하지 않는다."""
    config = _write_config(tmp_path, tmp_path / "recipes")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_recipe_generate",
        lambda args, **kwargs: json.dumps({"ok": True, "name": args["name"]}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(
        id="recipe-generate-1",
        name="recipe_generate",
        arguments={"action": "draft", "name": "market", "instructions": "x"},
    )

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "name": "market"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_recipe_generate_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 recipe_generate를 포함한다."""
    config = _write_config(tmp_path, tmp_path / "recipes")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("recipe generate 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "recipe_generate" in {tool.name for tool in request.tools}
