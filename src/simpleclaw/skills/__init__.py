"""Skill loader engine and MCP client."""

from simpleclaw.skills.models import (
    MCPConnectionError,
    RetryPolicy,
    SkillDefinition,
    SkillError,
    SkillExecutionError,
    SkillNotFoundError,
    SkillResult,
    SkillScope,
    SkillTimeoutError,
    ToolDefinition,
    ToolSource,
)
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.executor import execute_skill
from simpleclaw.skills.mcp_client import MCPManager


def list_all_tools(
    skills: list[SkillDefinition],
    mcp_manager: MCPManager | None = None,
) -> list[ToolDefinition]:
    """Combine skills and MCP tools into a unified list.

    Skills take priority over MCP tools with the same name.
    """
    tools: dict[str, ToolDefinition] = {}

    # MCP tools first (lower priority)
    if mcp_manager:
        for mcp_tool in mcp_manager.list_tools():
            tools[mcp_tool.name] = mcp_tool

    # Skills second (override MCP with same name)
    for skill in skills:
        tools[skill.name] = ToolDefinition(
            name=skill.name,
            description=skill.description,
            source=ToolSource.SKILL,
            source_name=skill.name,
        )

    return list(tools.values())


__all__ = [
    "MCPConnectionError",
    "MCPManager",
    "RetryPolicy",
    "SkillDefinition",
    "SkillError",
    "SkillExecutionError",
    "SkillNotFoundError",
    "SkillResult",
    "SkillScope",
    "SkillTimeoutError",
    "ToolDefinition",
    "ToolSource",
    "discover_skills",
    "execute_skill",
    "list_all_tools",
]
