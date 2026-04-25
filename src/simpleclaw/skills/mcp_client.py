"""MCP (Model Context Protocol) 클라이언트 관리자.

외부 MCP 서버에 연결하여 도구(tool)를 검색하고 호출하는 기능을 제공한다.

동작 흐름:
1. 설정에 정의된 MCP 서버들에 stdio 방식으로 연결
2. 각 서버에서 제공하는 도구 목록을 로드하여 내부 레지스트리에 등록
3. 에이전트 요청 시 해당 도구를 찾아 MCP 서버에 호출을 위임

설계 결정:
- 서버 연결 실패 시 해당 서버만 건너뛰고 나머지는 계속 연결 (부분 장애 허용)
- 도구 호출마다 새 세션을 생성하므로 장기 연결 유지 부담 없음
- mcp 패키지 미설치 시 ImportError를 MCPConnectionError로 변환하여 명확한 에러 메시지 제공
"""

from __future__ import annotations

import logging
from pathlib import Path

from simpleclaw.skills.models import (
    MCPConnectionError,
    ToolDefinition,
    ToolSource,
)

logger = logging.getLogger(__name__)


class MCPManager:
    """MCP 서버 연결을 관리하고 도구 접근을 제공하는 클래스."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}  # 도구 이름 -> 도구 정의
        self._connected_servers: list[str] = []  # 연결 성공한 서버 이름 목록
        self._server_configs: dict[str, dict] = {}  # 서버 이름 -> 설정 (재연결 시 사용)

    async def connect_servers(self, mcp_config: dict) -> None:
        """설정에 정의된 MCP 서버들에 연결한다.

        개별 서버 연결 실패 시 로그를 남기고 건너뛴다 — 에이전트는
        해당 서버의 도구 없이 계속 동작한다.

        Args:
            mcp_config: MCP 설정 딕셔너리 (servers 키 포함)
        """
        servers = mcp_config.get("servers", {})
        if not servers:
            logger.debug("No MCP servers configured.")
            return

        for name, server_conf in servers.items():
            try:
                await self._connect_server(name, server_conf)
            except Exception as e:
                logger.warning(
                    "Failed to connect to MCP server '%s': %s. "
                    "Agent will continue without this server's tools.",
                    name, e,
                )

    async def _connect_server(self, name: str, config: dict) -> None:
        """단일 MCP 서버에 연결하고 제공하는 도구들을 로드한다."""
        transport = config.get("transport", "stdio")
        command = config.get("command")

        if not command:
            raise MCPConnectionError(
                f"MCP server '{name}' has no command specified"
            )

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=command,
                args=config.get("args", []),
                env=config.get("env"),
            )

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools_result = await session.list_tools()
                    for tool in tools_result.tools:
                        tool_def = ToolDefinition(
                            name=tool.name,
                            description=tool.description or "",
                            source=ToolSource.MCP,
                            source_name=name,
                        )
                        self._tools[tool.name] = tool_def

            self._connected_servers.append(name)
            self._server_configs[name] = config
            logger.info(
                "Connected to MCP server '%s', loaded %d tools.",
                name, len(tools_result.tools),
            )

        except ImportError:
            raise MCPConnectionError(
                "MCP package not installed. Install with: pip install mcp"
            )
        except Exception as e:
            raise MCPConnectionError(
                f"Failed to connect to MCP server '{name}': {e}"
            ) from e

    async def call_tool(
        self, tool_name: str, arguments: dict | None = None
    ) -> str:
        """이름으로 MCP 도구를 실행하고 결과를 반환한다.

        Args:
            tool_name: 실행할 도구의 이름
            arguments: 도구에 전달할 인자 딕셔너리

        Returns:
            도구 실행 결과 텍스트

        Raises:
            MCPConnectionError: 도구를 찾을 수 없거나 실행에 실패한 경우
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise MCPConnectionError(f"MCP tool '{tool_name}' not found")

        server_name = tool.source_name
        server_conf = self._server_configs.get(server_name)
        if server_conf is None:
            raise MCPConnectionError(
                f"MCP server '{server_name}' config not found"
            )

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=server_conf["command"],
                args=server_conf.get("args", []),
                env=server_conf.get("env"),
            )

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        tool_name, arguments=arguments or {}
                    )
                    texts = []
                    for item in result.content:
                        if hasattr(item, "text"):
                            texts.append(item.text)
                    return "\n".join(texts) if texts else str(result)

        except ImportError:
            raise MCPConnectionError(
                "MCP package not installed. Install with: pip install mcp"
            )
        except Exception as e:
            raise MCPConnectionError(
                f"Failed to call MCP tool '{tool_name}': {e}"
            ) from e

    def list_tools(self) -> list[ToolDefinition]:
        """연결된 모든 MCP 서버의 도구 목록을 반환한다."""
        return list(self._tools.values())

    def get_connected_servers(self) -> list[str]:
        """연결에 성공한 서버들의 이름 목록을 반환한다."""
        return list(self._connected_servers)
