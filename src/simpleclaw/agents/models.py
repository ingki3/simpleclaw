"""Data models for the sub-agent spawner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class SubAgentStatus(Enum):
    """Status of a sub-agent."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    KILLED = "killed"


class SubAgentError(Exception):
    """Base error for sub-agent operations."""


class SpawnError(SubAgentError):
    """Raised when a sub-agent cannot be spawned."""


class PoolExhaustedError(SubAgentError):
    """Raised when the concurrency pool is full and no queue is available."""


@dataclass
class PermissionScope:
    """Constraints applied to a sub-agent."""

    allowed_paths: list[str] = field(default_factory=list)
    network: bool = False

    def to_dict(self) -> dict:
        return {
            "allowed_paths": self.allowed_paths,
            "network": self.network,
        }


@dataclass
class SubAgent:
    """A spawned subprocess representing a delegated task."""

    agent_id: str
    task: str
    command: list[str]
    scope: PermissionScope
    workspace_path: Path | None = None
    status: SubAgentStatus = SubAgentStatus.PENDING
    spawn_time: datetime = field(default_factory=datetime.now)
    end_time: datetime | None = None
    exit_code: int | None = None
    timeout: int = 300


@dataclass
class SubAgentResult:
    """Parsed output from a completed sub-agent."""

    agent_id: str
    status: str
    data: dict | None = None
    error: str | None = None
    exit_code: int = 0
    execution_time: float = 0.0
