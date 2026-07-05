"""asset_inventory 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.asset_inventory import handle_asset_inventory
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.mcp_client import MCPManager


def test_asset_inventory_is_operator_scoped_only():
    """기본 runtime build에는 보이지 않고 operator gate가 열릴 때만 노출된다."""
    runtime_names = {tool.name for tool in build_tool_definitions(skills=[])}
    operator_names = {
        tool.name
        for tool in build_tool_definitions(
            skills=[],
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR),
            operator_gate=True,
        )
    }

    assert "asset_inventory" not in runtime_names
    assert "asset_inventory" in operator_names


def test_asset_inventory_native_tools_include_scope_risk_and_enabled():
    """native tool inventory는 registry metadata와 operator 노출 여부를 함께 보여준다."""
    payload = json.loads(handle_asset_inventory({"type": "native_tools"}))
    tools = {tool["name"]: tool for tool in payload["sections"]["native_tools"]}

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert tools["web_fetch"]["scope"] == "runtime"
    assert tools["web_fetch"]["risk"] == "low"
    assert tools["web_fetch"]["enabled"] is True
    assert tools["asset_inventory"]["scope"] == "operator"
    assert tools["asset_inventory"]["risk"] == "low"
    assert tools["asset_inventory"]["operator_gate_required"] is True


def test_asset_inventory_skill_paths_show_runtime_source_dir_and_executable(tmp_path):
    """SimpleClaw runtime skill과 Hermes skill이 헷갈리지 않도록 source_dir/path를 명시한다."""
    local_dir = tmp_path / "runtime-skills"
    skill_dir = local_dir / "market"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    script = scripts_dir / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    script.chmod(0o755)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: market\n"
        "description: market report\n"
        "---\n\n"
        "## When to use\n시장 리포트\n\n"
        "```bash\npython scripts/run.py\n```\n",
        encoding="utf-8",
    )
    global_dir = tmp_path / "global-skills"
    config = tmp_path / "config.yaml"
    config.write_text(
        f"skills:\n  local_dir: {local_dir}\n  global_dir: {global_dir}\n",
        encoding="utf-8",
    )
    skills = discover_skills(local_dir, global_dir)

    payload = json.loads(
        handle_asset_inventory(
            {"type": "skills", "include_paths": True},
            config_path=config,
            skills=skills,
        )
    )
    item = payload["sections"]["skills"][0]

    assert item["name"] == "market"
    assert item["source"] == "simpleclaw_runtime_skill"
    assert item["scope"] == "local"
    assert item["source_dir"] == str(local_dir)
    assert item["skill_dir"] == str(skill_dir)
    assert item["script_path"] == str(script)
    assert item["executable"] is True


def test_asset_inventory_recipe_parse_status_and_error_reporting(tmp_path):
    """recipe inventory는 유효/무효 recipe.yaml의 path와 parse status를 보여준다."""
    recipes_dir = tmp_path / "recipes"
    good_dir = recipes_dir / "good"
    bad_dir = recipes_dir / "bad"
    good_dir.mkdir(parents=True)
    bad_dir.mkdir(parents=True)
    (good_dir / "recipe.yaml").write_text(
        "name: good\ndescription: ok\ninstructions: do it\n",
        encoding="utf-8",
    )
    (bad_dir / "recipe.yaml").write_text(
        "description: missing name\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(f"recipes:\n  dir: {recipes_dir}\n", encoding="utf-8")
    recipes = [load_recipe(good_dir / "recipe.yaml")]

    payload = json.loads(
        handle_asset_inventory(
            {"type": "recipes", "include_paths": True, "include_errors": True},
            config_path=config,
            recipes=recipes,
        )
    )
    items = {item["directory"]: item for item in payload["sections"]["recipes"]}

    assert items["good"]["parse_status"] == "ok"
    assert items["good"]["path"] == str(good_dir / "recipe.yaml")
    assert items["bad"]["parse_status"] == "error"
    assert "missing 'name'" in items["bad"]["error"]


def test_asset_inventory_selector_config_and_mcp_summary(tmp_path):
    """selector config와 MCP 연결/도구 mock 상태를 읽기 전용으로 요약한다."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "agent:\n"
        "  asset_selection:\n"
        "    enabled: true\n"
        "    backend: gemini\n"
        "    skill_top_k: 7\n"
        "mcp:\n"
        "  servers:\n"
        "    browser:\n"
        "      command: mcp-browser\n",
        encoding="utf-8",
    )
    mcp_manager = MCPManager()
    mcp_manager._connected_servers = ["browser"]
    mcp_manager._server_configs = {"browser": {"command": "mcp-browser"}}
    tool = MagicMock()
    tool.name = "open_page"
    tool.description = "open"
    tool.source_name = "browser"
    mcp_manager._tools = {"open_page": tool}

    payload = json.loads(
        handle_asset_inventory(
            {"type": "all", "include_paths": True},
            config_path=config,
            mcp_manager=mcp_manager,
        )
    )

    assert payload["sections"]["selector"]["config"]["enabled"] is True
    assert payload["sections"]["selector"]["config"]["skill_top_k"] == 7
    assert payload["sections"]["mcp"]["configured_servers"] == ["browser"]
    assert payload["sections"]["mcp"]["connected_servers"] == ["browser"]
    # metadata가 없는 tool은 operator scope/schema 없음으로 fail-closed 요약된다.
    assert payload["sections"]["mcp"]["tools"] == [
        {
            "name": "open_page",
            "source_name": "browser",
            "scope": "operator",
            "has_input_schema": False,
        }
    ]
    assert payload["sections"]["mcp"]["connection_errors"] == {}


@pytest.mark.asyncio
async def test_orchestrator_asset_inventory_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 asset_inventory를 실행하지 않는다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_asset_inventory",
        lambda args, **kwargs: json.dumps({"ok": True, "type": args["type"]}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="asset-1", name="asset_inventory", arguments={"type": "all"})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "type": "all"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_asset_inventory_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 asset_inventory를 포함한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("asset inventory 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "asset_inventory" in {tool.name for tool in request.tools}