"""Tests for MCP client manager."""

import sys
import types

import pytest

from simpleclaw.skills.mcp_client import MCPManager


class _FakeAsyncContext:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeStdioServerParameters:
    def __init__(self, command, args=None, env=None):
        self.command = command
        self.args = args or []
        self.env = env


class _FakeClientSession:
    registry = {}

    def __init__(self, read, write):
        self.state = self.registry[read]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def initialize(self):
        self.state["initialize_calls"] += 1
        if self.state.get("initialize_error"):
            raise self.state["initialize_error"]
        return self.state["initialize_result"]

    async def list_tools(self):
        self.state["list_tools_calls"] += 1
        if self.state.get("list_tools_error"):
            raise self.state["list_tools_error"]
        return types.SimpleNamespace(tools=self.state["tools"])


@pytest.fixture
def fake_mcp(monkeypatch):
    """mcp 패키지 없이 MCP 세션 동작을 검증하기 위한 최소 fake 모듈."""
    _FakeClientSession.registry = {}

    mcp_module = types.ModuleType("mcp")
    mcp_module.ClientSession = _FakeClientSession
    mcp_module.StdioServerParameters = _FakeStdioServerParameters

    client_module = types.ModuleType("mcp.client")
    stdio_module = types.ModuleType("mcp.client.stdio")

    def stdio_client(server_params):
        return _FakeAsyncContext((server_params.command, object()))

    stdio_module.stdio_client = stdio_client

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)

    return _FakeClientSession.registry


def _server_state(*, tools_capability, tools=None, list_tools_error=None):
    capabilities = types.SimpleNamespace()
    if tools_capability:
        capabilities.tools = types.SimpleNamespace()
    initialize_result = types.SimpleNamespace(capabilities=capabilities)
    return {
        "initialize_calls": 0,
        "initialize_result": initialize_result,
        "list_tools_calls": 0,
        "list_tools_error": list_tools_error,
        "tools": tools or [],
    }


class TestMCPManager:
    @pytest.mark.asyncio
    async def test_empty_config(self):
        """No servers configured — manager initializes with empty tools."""
        manager = MCPManager()
        await manager.connect_servers({"servers": {}})
        assert manager.list_tools() == []
        assert manager.get_connected_servers() == []

    @pytest.mark.asyncio
    async def test_no_servers_key(self):
        """Config without servers key — gracefully handles."""
        manager = MCPManager()
        await manager.connect_servers({})
        assert manager.list_tools() == []

    @pytest.mark.asyncio
    async def test_connection_failure_graceful(self):
        """Failed server connection should not crash the manager."""
        manager = MCPManager()
        config = {
            "servers": {
                "bad-server": {
                    "command": "nonexistent_mcp_server_xyz",
                    "transport": "stdio",
                }
            }
        }
        # Should not raise — failures are logged and skipped
        await manager.connect_servers(config)
        assert manager.list_tools() == []
        assert manager.get_connected_servers() == []

    @pytest.mark.asyncio
    async def test_missing_command_graceful(self):
        """Server without command should be skipped."""
        manager = MCPManager()
        config = {
            "servers": {
                "no-cmd": {
                    "transport": "stdio",
                }
            }
        }
        await manager.connect_servers(config)
        assert manager.list_tools() == []

    def test_initial_state(self):
        """Fresh manager starts with no tools or servers."""
        manager = MCPManager()
        assert manager.list_tools() == []
        assert manager.get_connected_servers() == []

    @pytest.mark.asyncio
    async def test_prompt_only_server_connects_without_listing_tools(
        self, fake_mcp
    ):
        """tools capability가 없으면 list_tools 없이 연결 서버로 기록한다."""
        fake_mcp["prompt-only"] = _server_state(tools_capability=False)
        manager = MCPManager()

        await manager.connect_servers(
            {"servers": {"prompt-only": {"command": "prompt-only"}}}
        )

        assert fake_mcp["prompt-only"]["initialize_calls"] == 1
        assert fake_mcp["prompt-only"]["list_tools_calls"] == 0
        assert manager.get_connected_servers() == ["prompt-only"]
        assert manager.list_tools() == []

    @pytest.mark.asyncio
    async def test_tools_capability_keeps_existing_tool_registration(
        self, fake_mcp
    ):
        """tools capability가 있으면 기존처럼 list_tools 결과를 등록한다."""
        fake_mcp["tool-server"] = _server_state(
            tools_capability=True,
            tools=[types.SimpleNamespace(name="search", description="Search docs")],
        )
        manager = MCPManager()

        await manager.connect_servers(
            {"servers": {"tool-server": {"command": "tool-server"}}}
        )

        assert fake_mcp["tool-server"]["list_tools_calls"] == 1
        assert manager.get_connected_servers() == ["tool-server"]
        tools = manager.list_tools()
        assert [tool.name for tool in tools] == ["search"]
        assert tools[0].source_name == "tool-server"

    @pytest.mark.asyncio
    async def test_list_tools_failure_skips_only_that_server(
        self, fake_mcp
    ):
        """list_tools 실패는 해당 서버만 실패 처리하고 다음 서버 연결은 계속한다."""
        fake_mcp["bad-tools"] = _server_state(
            tools_capability=True,
            list_tools_error=RuntimeError("tools/list unavailable"),
        )
        fake_mcp["prompt-only"] = _server_state(tools_capability=False)
        manager = MCPManager()

        await manager.connect_servers(
            {
                "servers": {
                    "bad-tools": {"command": "bad-tools"},
                    "prompt-only": {"command": "prompt-only"},
                }
            }
        )

        assert fake_mcp["bad-tools"]["list_tools_calls"] == 1
        assert fake_mcp["prompt-only"]["list_tools_calls"] == 0
        assert manager.get_connected_servers() == ["prompt-only"]
        assert manager.list_tools() == []
