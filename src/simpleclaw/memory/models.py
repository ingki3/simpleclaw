"""Data models for the memory system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageRole(Enum):
    """Role in a conversation message."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ConversationMessage:
    """A single message in conversation history."""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    token_count: int = 0


@dataclass
class MemoryEntry:
    """A core memory entry produced by the dreaming process."""
    summary: str
    created_at: datetime = field(default_factory=datetime.now)
    source: str = ""  # e.g., "dreaming_2026-04-17"


class MemoryError(Exception):
    """Base class for memory errors."""


class DreamingError(MemoryError):
    """Error during the dreaming process."""
