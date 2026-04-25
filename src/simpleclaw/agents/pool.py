"""동시성 풀: 세마포어 기반 서브에이전트 동시 실행 제한.

asyncio.Semaphore를 사용하여 최대 동시 실행 수를 제어한다.
슬롯이 모두 사용 중이면 acquire()에서 대기하며, 대기 중인 작업 수도 추적한다.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ConcurrencyPool:
    """asyncio.Semaphore 기반 서브에이전트 동시 실행 슬롯 관리자.

    실행 중(running)·대기 중(queued)·가용(available) 슬롯 수를 실시간 추적한다.
    """

    def __init__(self, max_concurrent: int = 3) -> None:
        self._max = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = 0
        self._queued = 0

    async def acquire(self) -> None:
        """슬롯을 획득한다. 모든 슬롯이 사용 중이면 대기한다."""
        self._queued += 1
        await self._semaphore.acquire()
        self._queued -= 1
        self._running += 1

    def release(self) -> None:
        """슬롯을 반환한다."""
        if self._running > 0:
            self._running -= 1
        self._semaphore.release()

    @property
    def running_count(self) -> int:
        return self._running

    @property
    def queued_count(self) -> int:
        return self._queued

    @property
    def available_slots(self) -> int:
        return self._max - self._running

    @property
    def max_concurrent(self) -> int:
        return self._max

    def get_status(self) -> dict:
        """풀의 현재 상태(실행 중/대기 중/최대)를 딕셔너리로 반환한다."""
        return {
            "running": self._running,
            "queued": self._queued,
            "max": self._max,
        }
