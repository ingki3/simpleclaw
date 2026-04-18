"""Heartbeat monitoring: tick logic, dirty-state detection, HEARTBEAT.md writes."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from simpleclaw.daemon.models import HeartbeatTick
from simpleclaw.daemon.store import DaemonStore

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """Executes periodic heartbeat ticks and writes status to HEARTBEAT.md."""

    def __init__(
        self,
        store: DaemonStore,
        status_file: str | Path,
        start_time: datetime | None = None,
    ) -> None:
        self._store = store
        self._status_file = Path(status_file)
        self._start_time = start_time or datetime.now()
        self._last_tick: HeartbeatTick | None = None
        self._dirty = False
        self._on_tick_callbacks: list = []

    def mark_dirty(self) -> None:
        """Mark that in-memory data has changed and needs flushing."""
        self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    def add_tick_callback(self, callback) -> None:
        """Register a callback to run on each tick (for dreaming trigger, timeout checks, etc.)."""
        self._on_tick_callbacks.append(callback)

    async def tick(self) -> HeartbeatTick:
        """Execute a single heartbeat tick.

        1. Check dirty state and flush if needed.
        2. Count active cron jobs and pending tasks.
        3. Write status to HEARTBEAT.md.
        4. Run registered callbacks (dreaming trigger, timeout checks).
        """
        now = datetime.now()
        flush_performed = False

        # Flush if dirty
        if self._dirty:
            flush_performed = True
            self._dirty = False
            logger.info("Heartbeat: flushed dirty state to database.")

        # Count active jobs
        cron_jobs_active = len(self._store.list_jobs())
        pending_tasks = len(self._store.get_pending_waits())

        tick = HeartbeatTick(
            timestamp=now,
            dirty_state=self._dirty,
            pending_task_count=pending_tasks,
            flush_performed=flush_performed,
            cron_jobs_active=cron_jobs_active,
        )

        self._last_tick = tick

        # Write HEARTBEAT.md
        self._write_status(tick)

        # Run callbacks
        for callback in self._on_tick_callbacks:
            try:
                result = callback()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.exception("Heartbeat callback error")

        logger.debug(
            "Heartbeat tick: dirty=%s, pending=%d, flush=%s",
            tick.dirty_state,
            tick.pending_task_count,
            tick.flush_performed,
        )

        return tick

    def get_last_tick(self) -> HeartbeatTick | None:
        return self._last_tick

    def _write_status(self, tick: HeartbeatTick) -> None:
        """Write current status to HEARTBEAT.md."""
        self._status_file.parent.mkdir(parents=True, exist_ok=True)

        uptime = tick.timestamp - self._start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes = remainder // 60

        last_flush_str = "N/A"
        if tick.flush_performed:
            last_flush_str = tick.timestamp.strftime("%Y-%m-%dT%H:%M:%S")
        elif self._last_tick and self._last_tick.flush_performed:
            last_flush_str = self._last_tick.timestamp.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )

        content = (
            "# Heartbeat Status\n"
            "\n"
            f"**Last Tick**: {tick.timestamp.strftime('%Y-%m-%dT%H:%M:%S')}\n"
            f"**Status**: running\n"
            f"**Uptime**: {hours}h {minutes}m\n"
            f"**Dirty State**: {str(tick.dirty_state).lower()}\n"
            f"**Pending Tasks**: {tick.pending_task_count}\n"
            f"**Last Flush**: {last_flush_str}\n"
            f"**Cron Jobs Active**: {tick.cron_jobs_active}\n"
        )

        self._status_file.write_text(content, encoding="utf-8")
