"""MCP (Model Context Protocol) client manager."""

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
    """Manages connections to MCP servers and provides tool access."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._connected_servers: list[str] = []
        self._server_configs: dict[str, dict] = {}

    async def connect_servers(self, mcp_config: dict) -> None:
        """Connect to MCP servers defined in config.

        Failures are logged and skipped — the agent continues without
        that server's tools.
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
        """Connect to a single MCP server and load its tools."""
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
        """Execute an MCP tool by name and return the result."""
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
        """Return all tools from connected MCP servers."""
        return list(self._tools.values())

    def get_connected_servers(self) -> list[str]:
        """Return names of successfully connected servers."""
        return list(self._connected_servers)
