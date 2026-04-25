"""에이전트 데몬: PID 잠금, 이벤트 루프, 스케줄러를 관리하는 메인 오케스트레이터.

데몬의 생명주기를 관리한다:
1. PID 파일로 단일 인스턴스를 보장 (중복 실행 방지)
2. APScheduler 기반 하트비트 틱으로 주기적 상태 점검
3. 크론 작업, 대기 상태, 드리밍 트리거 등 서브시스템 초기화 및 조율

설계 결정:
- PID 파일 기반 잠금: 외부 의존 없이 프로세스 단일성 보장
- 컴포넌트 지연 초기화: start() 호출 시에만 리소스 할당
- 비동기 콜백 체인: 하트비트 틱에 드리밍/타임아웃 검사를 연결
"""

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
    """하트비트, 스케줄러, 이벤트 루프를 호스팅하는 영구 백그라운드 데몬."""

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
        """데몬 저장소 인스턴스를 반환한다. 데몬 미시작 시 RuntimeError."""
        if self._store is None:
            raise RuntimeError("Daemon not started")
        return self._store

    @property
    def heartbeat(self) -> HeartbeatMonitor:
        """하트비트 모니터 인스턴스를 반환한다. 데몬 미시작 시 RuntimeError."""
        if self._heartbeat is None:
            raise RuntimeError("Daemon not started")
        return self._heartbeat

    @property
    def cron_scheduler(self) -> CronScheduler:
        """크론 스케줄러 인스턴스를 반환한다. 데몬 미시작 시 RuntimeError."""
        if self._cron_scheduler is None:
            raise RuntimeError("Daemon not started")
        return self._cron_scheduler

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """APScheduler 인스턴스를 반환한다. 데몬 미시작 시 RuntimeError."""
        if self._scheduler is None:
            raise RuntimeError("Daemon not started")
        return self._scheduler

    @property
    def config(self) -> dict:
        """데몬 설정 딕셔너리를 반환한다."""
        return self._config

    @property
    def dreaming_trigger(self) -> DreamingTrigger | None:
        """드리밍 트리거 인스턴스를 반환한다 (미설정 시 None)."""
        return self._dreaming_trigger

    @property
    def wait_state_manager(self) -> WaitStateManager | None:
        """대기 상태 관리자 인스턴스를 반환한다 (미시작 시 None)."""
        return self._wait_state_manager

    def is_running(self) -> bool:
        """데몬이 현재 실행 중인지 여부를 반환한다."""
        return self._running

    def setup_dreaming(
        self,
        conversation_store,
        dreaming_pipeline,
    ) -> None:
        """자동 드리밍 트리거를 설정한다 (start() 이후에 호출해야 함)."""
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
        """데몬을 시작한다: PID 잠금 획득, 컴포넌트 초기화, 틱 루프 시작."""
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

            # DB에 저장된 크론 작업을 APScheduler에 복원
            self._cron_scheduler.load_persisted_jobs()

            # 대기 상태 관리자 초기화 및 타임아웃 검사 등록
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

            # 시작 직후 초기 틱 1회 실행
            await self._heartbeat_tick()

        except Exception:
            self._release_lock()
            raise

    async def stop(self) -> None:
        """데몬을 안전하게 종료한다."""
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
        """하트비트 모니터를 통해 틱을 실행한다."""
        if not self._running and self._heartbeat is not None:
            return
        if self._heartbeat is None:
            return

        try:
            await self._heartbeat.tick()
        except Exception:
            logger.exception("Error during heartbeat tick")

    def _acquire_lock(self) -> None:
        """PID 잠금 파일을 획득한다. 다른 인스턴스가 실행 중이면 DaemonLockError 발생."""
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
                # 오래된 PID 파일 — 해당 프로세스가 이미 종료됨
                logger.warning("Removing stale PID file.")

        self._pid_file.write_text(str(os.getpid()), encoding="utf-8")

    def _release_lock(self) -> None:
        """PID 잠금 파일을 해제한다."""
        try:
            if self._pid_file.exists():
                self._pid_file.unlink()
        except OSError:
            logger.warning("Failed to remove PID file.")

    def _timeout_check(self) -> None:
        """만료된 대기 상태를 점검한다."""
        if self._wait_state_manager is None:
            return
        try:
            self._wait_state_manager.check_timeouts()
        except Exception:
            logger.exception("Wait state timeout check failed")

    async def _dreaming_check(self) -> None:
        """드리밍 조건을 확인하고 충족 시 파이프라인을 실행한다."""
        if self._dreaming_trigger is None:
            return
        try:
            if await self._dreaming_trigger.should_run():
                await self._dreaming_trigger.execute()
        except Exception:
            logger.exception("Dreaming check failed")
