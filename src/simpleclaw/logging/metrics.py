"""Metrics collector for agent monitoring."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MetricsSnapshot:
    """A snapshot of current metrics."""

    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_tokens_used: int = 0
    total_duration_ms: float = 0.0
    active_cron_jobs: int = 0
    sub_agent_spawns: int = 0
    error_rate: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "failed_executions": self.failed_executions,
            "total_tokens_used": self.total_tokens_used,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "active_cron_jobs": self.active_cron_jobs,
            "sub_agent_spawns": self.sub_agent_spawns,
            "error_rate": round(self.error_rate, 4),
            "timestamp": self.timestamp,
        }


class MetricsCollector:
    """Thread-safe metrics collector for agent operations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_executions = 0
        self._successful = 0
        self._failed = 0
        self._total_tokens = 0
        self._total_duration_ms = 0.0
        self._sub_agent_spawns = 0
        self._active_cron_jobs = 0

    def record_execution(
        self,
        success: bool,
        duration_ms: float = 0.0,
        tokens_used: int = 0,
    ) -> None:
        with self._lock:
            self._total_executions += 1
            if success:
                self._successful += 1
            else:
                self._failed += 1
            self._total_duration_ms += duration_ms
            self._total_tokens += tokens_used

    def record_sub_agent_spawn(self) -> None:
        with self._lock:
            self._sub_agent_spawns += 1

    def set_active_cron_jobs(self, count: int) -> None:
        with self._lock:
            self._active_cron_jobs = count

    def get_snapshot(self) -> MetricsSnapshot:
        with self._lock:
            error_rate = (
                self._failed / self._total_executions
                if self._total_executions > 0
                else 0.0
            )
            return MetricsSnapshot(
                total_executions=self._total_executions,
                successful_executions=self._successful,
                failed_executions=self._failed,
                total_tokens_used=self._total_tokens,
                total_duration_ms=self._total_duration_ms,
                active_cron_jobs=self._active_cron_jobs,
                sub_agent_spawns=self._sub_agent_spawns,
                error_rate=error_rate,
                timestamp=datetime.now().isoformat(),
            )

    def reset(self) -> None:
        with self._lock:
            self._total_executions = 0
            self._successful = 0
            self._failed = 0
            self._total_tokens = 0
            self._total_duration_ms = 0.0
            self._sub_agent_spawns = 0
