"""MCP tool dispatch tests.

``mcp_call``이 orchestrator의 ``_mcp_manager.call_tool()``로 정확히 라우팅되고,
manager 부재/인자 오류/scope 파생이 안전하게 처리되는지 검증한다.
"""

from types import SimpleNamespace

import pytest

from simpleclaw.agent.tool_dispatch import dispatch_tool_call
from simpleclaw.llm.models import ToolCall


class FakeMCPManager:
    def __init__(self, *, error: Exception | None = None):
        self.calls = []
        self._error = error

    async def call_tool(self, server_name, tool_name, arguments=None, *, scope="runtime"):
        self.calls.append((server_name, tool_name, arguments, scope))
        if self._error is not None:
            raise self._error
        return "mcp-ok"


@pytest.mark.asyncio
async def test_dispatch_mcp_call_to_manager():
    manager = FakeMCPManager()
    orch = SimpleNamespace(_mcp_manager=manager)
    call = ToolCall(
        id="1",
        name="mcp_call",
        arguments={"server": "time", "tool": "now", "arguments": {"tz": "Asia/Seoul"}},
    )

    result = await dispatch_tool_call(orch, call)

    assert result == "mcp-ok"
    assert manager.calls == [("time", "now", {"tz": "Asia/Seoul"}, "runtime")]


@pytest.mark.asyncio
async def test_dispatch_mcp_call_uses_operator_scope_in_operator_context():
    manager = FakeMCPManager()
    orch = SimpleNamespace(_mcp_manager=manager)
    call = ToolCall(id="1", name="mcp_call", arguments={"server": "gh", "tool": "search"})

    result = await dispatch_tool_call(orch, call, operator_tools=True)

    assert result == "mcp-ok"
    assert manager.calls == [("gh", "search", {}, "operator")]


@pytest.mark.asyncio
async def test_dispatch_mcp_call_reports_missing_manager():
    orch = SimpleNamespace(_mcp_manager=None)
    call = ToolCall(id="1", name="mcp_call", arguments={"server": "time", "tool": "now"})

    result = await dispatch_tool_call(orch, call)

    assert "MCP is not initialized" in result


@pytest.mark.asyncio
async def test_dispatch_mcp_call_requires_server_and_tool():
    manager = FakeMCPManager()
    orch = SimpleNamespace(_mcp_manager=manager)
    call = ToolCall(id="1", name="mcp_call", arguments={"server": "", "tool": "now"})

    result = await dispatch_tool_call(orch, call)

    assert "'server' and 'tool' arguments are required" in result
    assert manager.calls == []


@pytest.mark.asyncio
async def test_dispatch_mcp_call_rejects_non_object_arguments():
    manager = FakeMCPManager()
    orch = SimpleNamespace(_mcp_manager=manager)
    call = ToolCall(
        id="1",
        name="mcp_call",
        arguments={"server": "time", "tool": "now", "arguments": "not-a-dict"},
    )

    result = await dispatch_tool_call(orch, call)

    assert "'arguments' must be a JSON object" in result
    assert manager.calls == []


@pytest.mark.asyncio
async def test_dispatch_mcp_call_wraps_manager_errors():
    manager = FakeMCPManager(error=RuntimeError("boom"))
    orch = SimpleNamespace(_mcp_manager=manager)
    call = ToolCall(id="1", name="mcp_call", arguments={"server": "time", "tool": "now"})

    result = await dispatch_tool_call(orch, call)

    assert result.startswith("Error: MCP tool call failed:")
    assert "boom" in result
