"""Data models for the daemon subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ActionType(Enum):
    """Type of action a cron job executes."""

    PROMPT = "prompt"
    RECIPE = "recipe"


class ExecutionStatus(Enum):
    """Status of a cron job execution."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class DaemonError(Exception):
    """Base error for daemon operations."""


class DaemonLockError(DaemonError):
    """Raised when the daemon PID lock cannot be acquired."""


class CronJobNotFoundError(DaemonError):
    """Raised when a cron job is not found."""


class WaitStateNotFoundError(DaemonError):
    """Raised when a wait state is not found."""


@dataclass
class CronJob:
    """A user-defined scheduled task."""

    name: str
    cron_expression: str
    action_type: ActionType
    action_reference: str
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class CronJobExecution:
    """A record of a single cron job run."""

    job_name: str
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.RUNNING
    result_summary: str = ""
    error_details: str = ""
    id: int | None = None


@dataclass
class HeartbeatTick:
    """A snapshot of daemon state at a tick."""

    timestamp: datetime = field(default_factory=datetime.now)
    dirty_state: bool = False
    pending_task_count: int = 0
    flush_performed: bool = False
    cron_jobs_active: int = 0


@dataclass
class WaitState:
    """A serialized paused task."""

    task_id: str
    serialized_state: str
    condition_type: str
    registered_at: datetime = field(default_factory=datetime.now)
    timeout_seconds: int = 3600
    resolved_at: datetime | None = None
    resolution: str | None = None


@dataclass
class DaemonState:
    """Key-value singleton state entry."""

    key: str
    value: str
