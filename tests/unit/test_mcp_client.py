"""Tests for MCP client manager."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from simpleclaw.skills.mcp_client import MCPManager


class _AsyncContextManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _install_fake_mcp(monkeypatch, server_behaviors):
    sessions = []
    active_server = {"name": None}

    class FakeSession:
        def __init__(self, read, write):
            self.server_name = active_server["name"]
            self.behavior = server_behaviors[self.server_name]
            self.list_tools_calls = 0
            sessions.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def initialize(self):
            if self.behavior.get("initialize_error"):
                raise RuntimeError(self.behavior["initialize_error"])
            return self.behavior["initialize_result"]

        async def list_tools(self):
            self.list_tools_calls += 1
            if self.behavior.get("list_tools_error"):
                raise RuntimeError(self.behavior["list_tools_error"])
            return SimpleNamespace(tools=self.behavior.get("tools", []))

    def stdio_client(server_params):
        active_server["name"] = server_params.command
        return _AsyncContextManager((object(), object()))

    mcp_module = ModuleType("mcp")

    class FakeServerParameters:
        def __init__(self, command, args=None, env=None):
            self.command = command
            self.args = args or []
            self.env = env

    mcp_module.ClientSession = FakeSession
    mcp_module.StdioServerParameters = FakeServerParameters

    client_module = ModuleType("mcp.client")
    stdio_module = ModuleType("mcp.client.stdio")
    stdio_module.stdio_client = stdio_client

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)

    return sessions


def _initialize_result_with_capabilities(**capabilities):
    return SimpleNamespace(capabilities=SimpleNamespace(**capabilities))


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
    async def test_prompt_only_server_connects_without_listing_tools(self, monkeypatch):
        """tools capability가 없는 MCP 서버도 연결 성공으로 기록한다."""
        sessions = _install_fake_mcp(
            monkeypatch,
            {
                "prompt-only-command": {
                    "initialize_result": _initialize_result_with_capabilities(
                        prompts=SimpleNamespace(listChanged=False)
                    ),
                }
            },
        )
        manager = MCPManager()

        await manager.connect_servers(
            {"servers": {"prompt-only": {"command": "prompt-only-command"}}}
        )

        assert manager.get_connected_servers() == ["prompt-only"]
        assert manager.list_tools() == []
        assert sessions[0].list_tools_calls == 0

    @pytest.mark.asyncio
    async def test_tools_capability_keeps_loading_tools(self, monkeypatch):
        """tools capability가 있으면 기존처럼 도구 목록을 등록한다."""
        sessions = _install_fake_mcp(
            monkeypatch,
            {
                "tools-command": {
                    "initialize_result": _initialize_result_with_capabilities(
                        tools=SimpleNamespace(listChanged=False)
                    ),
                    "tools": [
                        SimpleNamespace(
                            name="search", description="Search through data"
                        )
                    ],
                }
            },
        )
        manager = MCPManager()

        await manager.connect_servers(
            {"servers": {"tools-server": {"command": "tools-command"}}}
        )

        tools = manager.list_tools()
        assert manager.get_connected_servers() == ["tools-server"]
        assert [tool.name for tool in tools] == ["search"]
        assert tools[0].source_name == "tools-server"
        assert sessions[0].list_tools_calls == 1

    @pytest.mark.asyncio
    async def test_list_tools_failure_skips_only_that_server(self, monkeypatch):
        """tools/list 실패는 해당 서버만 실패 처리하고 다른 서버 연결은 계속한다."""
        _install_fake_mcp(
            monkeypatch,
            {
                "bad-command": {
                    "initialize_result": _initialize_result_with_capabilities(
                        tools=SimpleNamespace(listChanged=False)
                    ),
                    "list_tools_error": "tools/list exploded",
                },
                "good-command": {
                    "initialize_result": _initialize_result_with_capabilities(
                        tools=SimpleNamespace(listChanged=False)
                    ),
                    "tools": [SimpleNamespace(name="ok", description="OK tool")],
                },
            },
        )
        manager = MCPManager()

        await manager.connect_servers(
            {
                "servers": {
                    "bad-server": {"command": "bad-command"},
                    "good-server": {"command": "good-command"},
                }
            }
        )

        assert manager.get_connected_servers() == ["good-server"]
        assert [tool.name for tool in manager.list_tools()] == ["ok"]
