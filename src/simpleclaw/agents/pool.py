"""Concurrency pool: semaphore-based limit on concurrent sub-agents."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class ConcurrencyPool:
    """Manages the pool of concurrent sub-agent slots using asyncio.Semaphore."""

    def __init__(self, max_concurrent: int = 3) -> None:
        self._max = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = 0
        self._queued = 0

    async def acquire(self) -> None:
        """Acquire a slot. Blocks if all slots are in use."""
        self._queued += 1
        await self._semaphore.acquire()
        self._queued -= 1
        self._running += 1

    def release(self) -> None:
        """Release a slot."""
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
        return {
            "running": self._running,
            "queued": self._queued,
            "max": self._max,
        }
