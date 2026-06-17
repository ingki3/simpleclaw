"""recipe_validate 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.recipe_validate import handle_recipe_validate
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall


def _write_config(tmp_path, recipes_dir):
    """테스트용 config.yaml을 작성한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"recipes:\n  dir: {recipes_dir}\n", encoding="utf-8")
    return config


def test_recipe_validate_is_not_exposed_to_runtime_context():
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

    assert "recipe_validate" not in runtime_names
    assert "recipe_validate" in operator_names


def test_recipe_validate_valid_recipe_renders_empty_and_non_empty_params(tmp_path):
    """유효한 recipe는 configured dir의 name resolve와 render smoke를 통과한다."""
    recipes_dir = tmp_path / "recipes"
    recipe_dir = recipes_dir / "market"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "recipe.yaml").write_text(
        "name: market\n"
        "description: report\n"
        "parameters:\n"
        "  - name: ticker\n"
        "    required: false\n"
        "    default: AAPL\n"
        "instructions: 'Report {{ ticker }} on {{ today }}'\n",
        encoding="utf-8",
    )
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(
        handle_recipe_validate(
            {"name": "market", "render_params": {"ticker": "TSLA"}},
            config_path=config,
        )
    )

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["recipe"]["name"] == "market"
    assert payload["recipe"]["path"] == str(recipe_dir / "recipe.yaml")
    assert payload["render"]["empty_params"]["ok"] is True
    assert payload["render"]["provided_params"]["ok"] is True
    assert "TSLA" in payload["render"]["provided_params"]["preview"]


def test_recipe_validate_reports_broken_yaml_and_missing_fields(tmp_path):
    """YAML/필수 필드 오류는 예외 대신 LLM-readable JSON error로 반환된다."""
    recipes_dir = tmp_path / "recipes"
    broken_dir = recipes_dir / "broken"
    missing_dir = recipes_dir / "missing"
    broken_dir.mkdir(parents=True)
    missing_dir.mkdir(parents=True)
    (broken_dir / "recipe.yaml").write_text("name: [unterminated\n", encoding="utf-8")
    (missing_dir / "recipe.yaml").write_text("description: no name\n", encoding="utf-8")
    config = _write_config(tmp_path, recipes_dir)

    broken = json.loads(handle_recipe_validate({"name": "broken"}, config_path=config))
    missing = json.loads(handle_recipe_validate({"name": "missing"}, config_path=config))

    assert broken["ok"] is False
    assert "Failed to parse" in broken["errors"][0]
    assert missing["ok"] is False
    assert "missing 'name'" in missing["errors"][0]


def test_recipe_validate_path_must_stay_under_configured_recipe_dir(tmp_path):
    """path 입력은 configured recipe dir 안의 recipe.yaml/yml로 제한한다."""
    recipes_dir = tmp_path / "recipes"
    outside = tmp_path / "outside" / "recipe.yaml"
    outside.parent.mkdir(parents=True)
    outside.write_text("name: outside\ninstructions: nope\n", encoding="utf-8")
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(handle_recipe_validate({"path": str(outside)}, config_path=config))

    assert payload["ok"] is False
    assert "configured recipes dir" in payload["errors"][0]


def test_recipe_validate_warns_when_recipe_name_collides_with_slash_command(tmp_path):
    """내장 slash command와 같은 recipe 이름은 실행되지 않을 수 있음을 warning으로 보여준다."""
    recipes_dir = tmp_path / "recipes"
    cron_dir = recipes_dir / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "recipe.yaml").write_text(
        "name: cron\ninstructions: collide\n",
        encoding="utf-8",
    )
    config = _write_config(tmp_path, recipes_dir)

    payload = json.loads(handle_recipe_validate({"name": "cron"}, config_path=config))

    assert payload["ok"] is True
    assert any("/cron" in warning for warning in payload["warnings"])


@pytest.mark.asyncio
async def test_orchestrator_recipe_validate_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 recipe_validate를 실행하지 않는다."""
    config = _write_config(tmp_path, tmp_path / "recipes")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_recipe_validate",
        lambda args, **kwargs: json.dumps({"ok": True, "name": args["name"]}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="recipe-validate-1", name="recipe_validate", arguments={"name": "market"})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "name": "market"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_recipe_validate_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 recipe_validate를 포함한다."""
    config = _write_config(tmp_path, tmp_path / "recipes")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("recipe validate 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "recipe_validate" in {tool.name for tool in request.tools}