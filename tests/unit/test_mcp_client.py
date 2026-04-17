"""Tests for MCP client manager."""

import pytest

from simpleclaw.skills.mcp_client import MCPManager
from simpleclaw.skills.models import ToolSource


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
