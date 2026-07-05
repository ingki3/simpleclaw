#!/usr/bin/env python3
"""SimpleClaw 통합 테스트용 최소 stdio MCP 서버.

real ``mcp`` SDK(FastMCP)로 tool 1개(echo)를 노출한다 — MCPManager의 stdio
discovery/call 경로가 실제 SDK wire format과 맞는지 smoke하는 용도.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("simpleclaw-test")


@mcp.tool()
def echo(text: str) -> str:
    """Echo text for integration tests."""
    return f"echo:{text}"


if __name__ == "__main__":
    mcp.run()
