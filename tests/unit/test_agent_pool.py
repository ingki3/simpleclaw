"""Tests for the concurrency pool."""

import asyncio

import pytest

from simpleclaw.agents.pool import ConcurrencyPool


class TestConcurrencyPool:
    def test_initial_state(self):
        pool = ConcurrencyPool(max_concurrent=3)
        assert pool.running_count == 0
        assert pool.available_slots == 3
        assert pool.max_concurrent == 3
        assert pool.queued_count == 0

    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        pool = ConcurrencyPool(max_concurrent=2)
        await pool.acquire()
        assert pool.running_count == 1
        assert pool.available_slots == 1

        await pool.acquire()
        assert pool.running_count == 2
        assert pool.available_slots == 0

        pool.release()
        assert pool.running_count == 1
        assert pool.available_slots == 1

    @pytest.mark.asyncio
    async def test_blocks_when_full(self):
        pool = ConcurrencyPool(max_concurrent=1)
        await pool.acquire()

        acquired = False

        async def try_acquire():
            nonlocal acquired
            await pool.acquire()
            acquired = True

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)
        assert not acquired  # Should be blocked

        pool.release()
        await asyncio.sleep(0.05)
        assert acquired  # Should now be acquired

        pool.release()
        task.cancel()

    def test_get_status(self):
        pool = ConcurrencyPool(max_concurrent=5)
        status = pool.get_status()
        assert status == {"running": 0, "queued": 0, "max": 5}

    @pytest.mark.asyncio
    async def test_multiple_release(self):
        pool = ConcurrencyPool(max_concurrent=2)
        await pool.acquire()
        pool.release()
        pool.release()  # Extra release should not go negative
        assert pool.running_count == 0
