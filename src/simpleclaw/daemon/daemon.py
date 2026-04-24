"""Agent daemon: main orchestrator with PID lock, event loop, and scheduler."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from simpleclaw.config import load_daemon_config
from simpleclaw.daemon.dreaming_trigger import DreamingTrigger
from simpleclaw.daemon.heartbeat import HeartbeatMonitor
from simpleclaw.daemon.models import DaemonLockError
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.daemon.wait_states import WaitStateManager

logger = logging.getLogger(__name__)


class AgentDaemon:
    """Persistent background daemon hosting heartbeat, scheduler, and event loop."""

    def __init__(
        self, config_path: str | Path, agent_orchestrator=None,
    ) -> None:
        self._config_path = Path(config_path)
        self._config = load_daemon_config(config_path)
        self._pid_file = Path(self._config["pid_file"])
        self._db_path = Path(self._config["db_path"])
        self._status_file = Path(self._config["status_file"])
        self._heartbeat_interval = self._config["heartbeat_interval"]

        self._agent = agent_orchestrator
        self._store: DaemonStore | None = None
        self._heartbeat: HeartbeatMonitor | None = None
        self._scheduler: AsyncIOScheduler | None = None
        self._cron_scheduler: CronScheduler | None = None
        self._dreaming_trigger: DreamingTrigger | None = None
        self._wait_state_manager: WaitStateManager | None = None
        self._running = False
        self._start_time: datetime | None = None

    @property
    def store(self) -> DaemonStore:
        if self._store is None:
            raise RuntimeError("Daemon not started")
        return self._store

    @property
    def heartbeat(self) -> HeartbeatMonitor:
        if self._heartbeat is None:
            raise RuntimeError("Daemon not started")
        return self._heartbeat

    @property
    def cron_scheduler(self) -> CronScheduler:
        if self._cron_scheduler is None:
            raise RuntimeError("Daemon not started")
        return self._cron_scheduler

    @property
    def scheduler(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            raise RuntimeError("Daemon not started")
        return self._scheduler

    @property
    def config(self) -> dict:
        return self._config

    @property
    def dreaming_trigger(self) -> DreamingTrigger | None:
        return self._dreaming_trigger

    @property
    def wait_state_manager(self) -> WaitStateManager | None:
        return self._wait_state_manager

    def is_running(self) -> bool:
        return self._running

    def setup_dreaming(
        self,
        conversation_store,
        dreaming_pipeline,
    ) -> None:
        """Configure automatic dreaming trigger (call after start())."""
        if self._store is None:
            raise RuntimeError("Daemon not started")

        dreaming_config = self._config.get("dreaming", {})
        self._dreaming_trigger = DreamingTrigger(
            conversation_store=conversation_store,
            dreaming_pipeline=dreaming_pipeline,
            daemon_store=self._store,
            overnight_hour=dreaming_config.get("overnight_hour", 3),
            idle_threshold=dreaming_config.get("idle_threshold", 7200),
        )
        if self._heartbeat:
            self._heartbeat.add_tick_callback(self._dreaming_check)

    async def start(self) -> None:
        """Start the daemon: acquire lock, init components, begin tick loop."""
        self._acquire_lock()

        try:
            self._start_time = datetime.now()
            self._store = DaemonStore(self._db_path)
            self._heartbeat = HeartbeatMonitor(
                self._store, self._status_file, self._start_time
            )

            self._scheduler = AsyncIOScheduler()
            self._cron_scheduler = CronScheduler(
                self._store, self._scheduler,
                agent_orchestrator=self._agent,
            )

            self._scheduler.add_job(
                self._heartbeat_tick,
                trigger=IntervalTrigger(seconds=self._heartbeat_interval),
                id="heartbeat",
                name="heartbeat-tick",
                replace_existing=True,
            )
            self._scheduler.start()
            self._running = True

            # Load persisted cron jobs
            self._cron_scheduler.load_persisted_jobs()

            # Setup wait state manager with timeout checking
            wait_config = self._config.get("wait_state", {})
            self._wait_state_manager = WaitStateManager(
                self._store,
                default_timeout=wait_config.get("default_timeout", 3600),
            )
            self._heartbeat.add_tick_callback(self._timeout_check)

            self._store.set_state(
                "daemon_start_time", self._start_time.isoformat()
            )

            logger.info(
                "Daemon started (PID=%d, interval=%ds)",
                os.getpid(),
                self._heartbeat_interval,
            )

            # Run initial tick
            await self._heartbeat_tick()

        except Exception:
            self._release_lock()
            raise

    async def stop(self) -> None:
        """Gracefully stop the daemon."""
        if not self._running:
            return

        self._running = False

        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)

        if self._store:
            self._store.close()

        self._release_lock()
        logger.info("Daemon stopped.")

    async def _heartbeat_tick(self) -> None:
        """Execute a heartbeat tick via the monitor."""
        if not self._running and self._heartbeat is not None:
            return
        if self._heartbeat is None:
            return

        try:
            await self._heartbeat.tick()
        except Exception:
            logger.exception("Error during heartbeat tick")

    def _acquire_lock(self) -> None:
        """Acquire PID lock file. Raises DaemonLockError if another instance is running."""
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)

        if self._pid_file.exists():
            try:
                existing_pid = int(self._pid_file.read_text().strip())
                # Check if process is still alive
                os.kill(existing_pid, 0)
                raise DaemonLockError(
                    f"Another daemon instance is running (PID {existing_pid})"
                )
            except (ValueError, ProcessLookupError, PermissionError):
                # Stale PID file — process is dead
                logger.warning("Removing stale PID file.")

        self._pid_file.write_text(str(os.getpid()), encoding="utf-8")

    def _release_lock(self) -> None:
        """Release PID lock file."""
        try:
            if self._pid_file.exists():
                self._pid_file.unlink()
        except OSError:
            logger.warning("Failed to remove PID file.")

    def _timeout_check(self) -> None:
        """Check for timed-out wait states."""
        if self._wait_state_manager is None:
            return
        try:
            self._wait_state_manager.check_timeouts()
        except Exception:
            logger.exception("Wait state timeout check failed")

    async def _dreaming_check(self) -> None:
        """Check dreaming conditions and execute if met."""
        if self._dreaming_trigger is None:
            return
        try:
            if await self._dreaming_trigger.should_run():
                await self._dreaming_trigger.execute()
        except Exception:
            logger.exception("Dreaming check failed")
