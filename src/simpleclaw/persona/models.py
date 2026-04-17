"""Data models for the persona parsing engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FileType(Enum):
    """Type of persona file."""
    AGENT = "agent"
    USER = "user"
    MEMORY = "memory"


class SourceScope(Enum):
    """Where the persona file was loaded from."""
    LOCAL = "local"
    GLOBAL = "global"


@dataclass(frozen=True)
class Section:
    """A single markdown heading and its body content."""
    level: int
    title: str
    content: str


@dataclass
class PersonaFile:
    """A parsed persona markdown file."""
    file_type: FileType
    source_path: str
    source_scope: SourceScope
    sections: list[Section] = field(default_factory=list)
    raw_content: str = ""


@dataclass
class PromptAssembly:
    """The assembled system prompt from multiple persona files."""
    parts: list[PersonaFile] = field(default_factory=list)
    assembled_text: str = ""
    token_count: int = 0
    token_budget: int = 0
    was_truncated: bool = False
