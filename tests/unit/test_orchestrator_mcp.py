"""AgentOrchestrator MCP integration tests.

lazy one-shot MCP 연결(``_ensure_mcp_connected``)과 context별 ``mcp_call``
tool 노출(runtime scope 필요, operator context 완화)을 검증한다.
"""

from types import SimpleNamespace
from typing import ClassVar

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator


class FakeMCPManager:
    instances: ClassVar[list["FakeMCPManager"]] = []

    def __init__(self, *_, **__):
        self.connected_with = None
        self.connect_calls = 0
        self.tool_scopes: list[str] = ["runtime"]
        FakeMCPManager.instances.append(self)

    async def connect_servers(self, config):
        self.connect_calls += 1
        self.connected_with = config

    def list_tools(self):
        return [SimpleNamespace(metadata={"scope": scope}) for scope in self.tool_scopes]

    def get_connected_servers(self):
        return ["fake"]

    def get_connection_errors(self):
        return {}


@pytest.fixture(autouse=True)
def _reset_fake_manager():
    FakeMCPManager.instances = []
    yield
    FakeMCPManager.instances = []


def _write_config(tmp_path, *, mcp_section: str) -> "object":
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: "gemini"
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key: "test-key"

agent:
  history_limit: 3
  db_path: "{tmp_path}/conversations.db"

skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: "AGENT.md"
      type: "agent"

memory:
  rag:
    enabled: false

{mcp_section}
""",
        encoding="utf-8",
    )
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir(exist_ok=True)
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir(exist_ok=True)
    (tmp_path / "global_skills").mkdir(exist_ok=True)
    return config


_MCP_ENABLED_SECTION = """
mcp:
  enabled: true
  servers:
    fake:
      command: fake-mcp
      scope: runtime
"""


@pytest.mark.asyncio
async def test_orchestrator_ensures_mcp_once(monkeypatch, tmp_path):
    config = _write_config(tmp_path, mcp_section=_MCP_ENABLED_SECTION)
    monkeypatch.setattr("simpleclaw.agent.orchestrator.MCPManager", FakeMCPManager)
    orch = AgentOrchestrator(config)

    await orch._ensure_mcp_connected()
    first_manager = orch._mcp_manager
    await orch._ensure_mcp_connected()

    assert first_manager is not None
    assert first_manager.connected_with["enabled"] is True
    assert "fake" in first_manager.connected_with["servers"]
    assert first_manager.connect_calls == 1
    assert first_manager is orch._mcp_manager


@pytest.mark.asyncio
async def test_orchestrator_skips_mcp_when_disabled(monkeypatch, tmp_path):
    config = _write_config(tmp_path, mcp_section="mcp:\n  enabled: false\n  servers: {}")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.MCPManager", FakeMCPManager)
    orch = AgentOrchestrator(config)

    await orch._ensure_mcp_connected()

    assert orch._mcp_manager is None
    assert FakeMCPManager.instances == []


@pytest.mark.asyncio
async def test_mcp_call_tool_included_when_runtime_mcp_tool_connected(monkeypatch, tmp_path):
    config = _write_config(tmp_path, mcp_section=_MCP_ENABLED_SECTION)
    monkeypatch.setattr("simpleclaw.agent.orchestrator.MCPManager", FakeMCPManager)
    orch = AgentOrchestrator(config)

    state = await orch._prepare_tool_loop_state(
        "hello",
        True,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        operator_tools=False,
    )

    assert "mcp_call" in {tool.name for tool in state.tools}


@pytest.mark.asyncio
async def test_operator_only_mcp_tool_hidden_from_runtime(monkeypatch, tmp_path):
    config = _write_config(tmp_path, mcp_section=_MCP_ENABLED_SECTION)
    monkeypatch.setattr("simpleclaw.agent.orchestrator.MCPManager", FakeMCPManager)
    orch = AgentOrchestrator(config)

    await orch._ensure_mcp_connected()
    orch._mcp_manager.tool_scopes = ["operator"]

    runtime_state = await orch._prepare_tool_loop_state(
        "hello",
        True,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        operator_tools=False,
    )
    operator_state = await orch._prepare_tool_loop_state(
        "hello",
        True,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        operator_tools=True,
    )

    assert "mcp_call" not in {tool.name for tool in runtime_state.tools}
    assert "mcp_call" in {tool.name for tool in operator_state.tools}


@pytest.mark.asyncio
async def test_mcp_call_tool_hidden_when_no_servers(monkeypatch, tmp_path):
    config = _write_config(tmp_path, mcp_section="mcp:\n  servers: {}")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.MCPManager", FakeMCPManager)
    orch = AgentOrchestrator(config)

    state = await orch._prepare_tool_loop_state(
        "hello",
        True,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        operator_tools=False,
    )

    assert "mcp_call" not in {tool.name for tool in state.tools}
