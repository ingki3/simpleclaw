"""Data models for the skill system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkillScope(Enum):
    """Where the skill was loaded from."""
    LOCAL = "local"
    GLOBAL = "global"


class ToolSource(Enum):
    """Source type of a tool."""
    SKILL = "skill"
    MCP = "mcp"


@dataclass
class SkillDefinition:
    """Metadata for a single skill parsed from SKILL.md."""
    name: str
    description: str = ""
    script_path: str = ""
    trigger: str = ""
    scope: SkillScope = SkillScope.LOCAL
    skill_dir: str = ""


@dataclass
class SkillResult:
    """Result of a skill execution."""
    output: str = ""
    exit_code: int = 0
    error: str = ""
    success: bool = True


@dataclass
class ToolDefinition:
    """Unified tool representation (skill or MCP)."""
    name: str
    description: str = ""
    source: ToolSource = ToolSource.SKILL
    source_name: str = ""


class SkillError(Exception):
    """Base class for skill errors."""


class SkillNotFoundError(SkillError):
    """Skill not found."""


class SkillExecutionError(SkillError):
    """Skill script execution failed."""


class SkillTimeoutError(SkillError):
    """Skill script timed out."""


class MCPConnectionError(SkillError):
    """MCP server connection failed."""
