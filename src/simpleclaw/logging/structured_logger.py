"""Structured execution logger with daily file rotation."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LogEntry:
    """A structured log entry for agent actions."""

    timestamp: str = ""
    level: str = "INFO"
    action_type: str = ""
    input_summary: str = ""
    output_summary: str = ""
    duration_ms: float = 0.0
    status: str = "success"
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class StructuredLogger:
    """Writes structured JSONL log entries to daily rotating files."""

    def __init__(self, log_dir: str | Path = ".logs") -> None:
        self._log_dir = Path(log_dir)
        self._current_date: str = ""
        self._current_file = None
        self._entry_count = 0

    def _ensure_dir(self) -> bool:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.warning("Cannot create log directory %s: %s", self._log_dir, exc)
            return False

    def _get_log_path(self) -> Path:
        date_str = datetime.now().strftime("%Y%m%d")
        return self._log_dir / f"execution_{date_str}.log"

    def log(
        self,
        action_type: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: float = 0.0,
        status: str = "success",
        level: str = "INFO",
        **details: object,
    ) -> LogEntry:
        """Write a structured log entry."""
        entry = LogEntry(
            timestamp=datetime.now().isoformat(),
            level=level,
            action_type=action_type,
            input_summary=input_summary[:500],
            output_summary=output_summary[:500],
            duration_ms=round(duration_ms, 2),
            status=status,
            details=details,
        )

        if not self._ensure_dir():
            return entry

        try:
            log_path = self._get_log_path()
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
            self._entry_count += 1
        except OSError:
            logger.warning("Failed to write log entry")

        return entry

    def get_entries(self, date: str | None = None, limit: int = 100) -> list[LogEntry]:
        """Read log entries from a specific date (default: today)."""
        if date is None:
            date = datetime.now().strftime("%Y%m%d")

        log_path = self._log_dir / f"execution_{date}.log"
        if not log_path.is_file():
            return []

        entries = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data = json.loads(line)
                            entries.append(LogEntry(**data))
                        except (json.JSONDecodeError, TypeError):
                            continue
        except OSError:
            pass

        return entries[-limit:]

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def log_dir(self) -> Path:
        return self._log_dir
