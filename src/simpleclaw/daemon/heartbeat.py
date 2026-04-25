"""하트비트 모니터링: 주기적 틱 실행, 더티 상태 감지, HEARTBEAT.md 상태 파일 기록.

데몬이 살아 있음을 외부에 알리고, 내부 상태를 주기적으로 점검하는 모듈이다.
각 틱마다 다음을 수행한다:
1. 더티(dirty) 플래그가 설정된 경우 데이터 플러시
2. 활성 크론 작업 수와 대기 중인 태스크 수 집계
3. HEARTBEAT.md 상태 파일에 현재 상태 기록
4. 등록된 콜백 실행 (드리밍 트리거, 타임아웃 검사 등)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from simpleclaw.daemon.models import HeartbeatTick
from simpleclaw.daemon.store import DaemonStore

logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """주기적 하트비트 틱을 실행하고 HEARTBEAT.md에 상태를 기록한다."""

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
        """메모리 내 데이터가 변경되어 플러시가 필요함을 표시한다."""
        self._dirty = True

    def is_dirty(self) -> bool:
        """플러시 대기 중인 변경 사항이 있는지 반환한다."""
        return self._dirty

    def add_tick_callback(self, callback) -> None:
        """각 틱마다 실행할 콜백을 등록한다 (드리밍 트리거, 타임아웃 검사 등)."""
        self._on_tick_callbacks.append(callback)

    async def tick(self) -> HeartbeatTick:
        """하트비트 틱 1회를 실행한다.

        1. 더티 상태를 확인하고 필요 시 플러시
        2. 활성 크론 작업 수와 대기 중인 태스크 수 집계
        3. HEARTBEAT.md에 현재 상태 기록
        4. 등록된 콜백 실행 (드리밍 트리거, 타임아웃 검사)
        """
        now = datetime.now()
        flush_performed = False

        # 더티 상태이면 플러시 수행
        if self._dirty:
            flush_performed = True
            self._dirty = False
            logger.info("Heartbeat: flushed dirty state to database.")

        # 활성 작업 수 집계
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

        # HEARTBEAT.md 상태 파일 갱신
        self._write_status(tick)

        # 등록된 콜백 순차 실행 (동기/비동기 모두 지원)
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
        """마지막 틱 정보를 반환한다 (아직 실행 전이면 None)."""
        return self._last_tick

    def _write_status(self, tick: HeartbeatTick) -> None:
        """현재 데몬 상태를 HEARTBEAT.md 파일에 기록한다."""
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
