"""config_inspect 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.config_inspect import handle_config_inspect
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall


def test_config_inspect_is_operator_scoped_only():
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

    assert "config_inspect" not in runtime_names
    assert "config_inspect" in operator_names


def test_config_inspect_filters_sections_resolves_paths_and_redacts(tmp_path, monkeypatch):
    """section 필터는 요청 섹션만 반환하고, path는 절대화하며, 시크릿 값은 숨긴다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n"
        "  default: claude\n"
        "  providers:\n"
        "    claude:\n"
        "      model: claude-3-5-sonnet\n"
        "      api_key: sk-liv...leak\n"
        "    openai:\n"
        "      api_key_env: OPENAI_API_KEY\n"
        "agent:\n"
        "  db_path: ~/runtime/conversations.db\n"
        "  workspace_dir: ~/runtime/workspace\n"
        "memory:\n"
        "  long_term:\n"
        "    insights_file: ~/runtime/insights.jsonl\n"
        "skills:\n"
        "  dir: ~/runtime/skills\n"
        "recipes:\n"
        "  dir: ~/runtime/recipes\n"
        "admin_api:\n"
        "  enabled: true\n"
        "  token_secret: file:admin_api_token\n"
        "security:\n"
        "  master_key: plain-master-key-should-not-leak\n",
        encoding="utf-8",
    )

    payload = json.loads(
        handle_config_inspect(
            {"section": "agent", "resolve_paths": True, "redact": True},
            config_path=config,
        )
    )

    assert payload["config_path"] == str(config)
    assert set(payload["sections"]) == {"agent"}
    assert payload["sections"]["agent"]["db_path"] == str(home / "runtime/conversations.db")
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "sk-liv" not in serialized
    assert "plain-master-key" not in serialized


def test_config_inspect_all_keeps_secret_references_but_masks_values(tmp_path, monkeypatch):
    """시크릿 참조 문자열은 운영자가 확인할 수 있게 보존하되 실제 값은 마스킹한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-secret-should-not-appear")
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n"
        "  providers:\n"
        "    claude:\n"
        "      api_key: env:ANTHROPIC_API_KEY\n"
        "    gemini:\n"
        "      api_key: AIzaSyPlainSecret1234567890\n"
        "admin_api:\n"
        "  token_secret: file:admin_api_token\n",
        encoding="utf-8",
    )

    payload = json.loads(handle_config_inspect({"section": "all"}, config_path=config))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["sections"]["admin_api"]["token_secret"] == "file:admin_api_token"
    assert payload["sections"]["llm"]["providers"]["claude"]["api_key"] == "env:ANTHROPIC_API_KEY"
    assert "env-secret-should-not-appear" not in serialized
    assert "AIzaSyPlainSecret" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_orchestrator_config_inspect_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 config_inspect를 실행하지 않는다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="config-1", name="config_inspect", arguments={"section": "agent"})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed)["sections"]["agent"]["db_path"] == str(tmp_path / "conversation.db")


@pytest.mark.asyncio
async def test_process_operator_message_exposes_config_inspect_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 config_inspect를 포함한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("config 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "config_inspect" in {tool.name for tool in request.tools}