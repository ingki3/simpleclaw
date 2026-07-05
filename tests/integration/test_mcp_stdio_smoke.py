"""stdio MCP smoke test using the real mcp SDK.

fake stdio MCP server(fixtures/fake_mcp_server.py)를 실제 subprocess로 띄워
MCPManager의 discovery → tool metadata → call 전체 경로를 검증한다.
"""

from pathlib import Path
import sys

import pytest

from simpleclaw.skills.mcp_client import MCPManager

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_stdio_mcp_discovery_and_call():
    server = Path(__file__).parents[1] / "fixtures" / "fake_mcp_server.py"
    manager = MCPManager()

    config = {
        "enabled": True,
        "servers": {
            "fake": {
                "command": sys.executable,
                "args": [str(server)],
                "scope": "runtime",
                "timeout": 30,
                "connect_timeout": 30,
            }
        },
    }
    await manager.connect_servers(config)

    assert manager.get_connection_errors() == {}
    assert manager.get_connected_servers() == ["fake"]
    tools = manager.list_tools()
    assert [(tool.source_name, tool.name) for tool in tools] == [("fake", "echo")]
    assert tools[0].metadata["scope"] == "runtime"
    assert tools[0].metadata["input_schema"].get("properties", {}).get("text")

    result = await manager.call_tool("fake", "echo", {"text": "ok"})
    assert "echo:ok" in result


@pytest.mark.asyncio
async def test_stdio_mcp_operator_scope_enforced_on_real_server():
    from simpleclaw.skills.models import MCPConnectionError

    server = Path(__file__).parents[1] / "fixtures" / "fake_mcp_server.py"
    manager = MCPManager()

    await manager.connect_servers(
        {
            "enabled": True,
            "servers": {
                "fake": {
                    "command": sys.executable,
                    "args": [str(server)],
                    # scope 미지정 → operator 기본값
                    "timeout": 30,
                    "connect_timeout": 30,
                }
            },
        }
    )

    assert manager.get_connected_servers() == ["fake"]
    with pytest.raises(MCPConnectionError, match="operator-only"):
        await manager.call_tool("fake", "echo", {"text": "ok"}, scope="runtime")

    result = await manager.call_tool("fake", "echo", {"text": "ok"}, scope="operator")
    assert "echo:ok" in result
