"""Tests for the heartbeat monitor."""

import pytest

from simpleclaw.daemon.heartbeat import HeartbeatMonitor
from simpleclaw.daemon.store import DaemonStore


class TestHeartbeatMonitor:
    @pytest.fixture
    def setup(self, tmp_path):
        db = tmp_path / "test.db"
        store = DaemonStore(db)
        status_file = tmp_path / "HEARTBEAT.md"
        monitor = HeartbeatMonitor(store, status_file)
        return store, monitor, status_file

    @pytest.mark.asyncio
    async def test_tick_writes_status_file(self, setup):
        _, monitor, status_file = setup
        tick = await monitor.tick()
        assert status_file.exists()
        content = status_file.read_text()
        assert "# Heartbeat Status" in content
        assert "**Status**: running" in content
        assert "**Last Tick**:" in content

    @pytest.mark.asyncio
    async def test_tick_returns_heartbeat_tick(self, setup):
        _, monitor, _ = setup
        tick = await monitor.tick()
        assert tick.timestamp is not None
        assert tick.dirty_state is False
        assert tick.pending_task_count == 0
        assert tick.flush_performed is False

    @pytest.mark.asyncio
    async def test_dirty_state_triggers_flush(self, setup):
        _, monitor, _ = setup
        monitor.mark_dirty()
        assert monitor.is_dirty() is True
        tick = await monitor.tick()
        assert tick.flush_performed is True
        assert monitor.is_dirty() is False

    @pytest.mark.asyncio
    async def test_clean_state_skips_flush(self, setup):
        _, monitor, _ = setup
        tick = await monitor.tick()
        assert tick.flush_performed is False

    @pytest.mark.asyncio
    async def test_get_last_tick(self, setup):
        _, monitor, _ = setup
        assert monitor.get_last_tick() is None
        await monitor.tick()
        assert monitor.get_last_tick() is not None

    @pytest.mark.asyncio
    async def test_tick_callback(self, setup):
        _, monitor, _ = setup
        called = []
        monitor.add_tick_callback(lambda: called.append(True))
        await monitor.tick()
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_tick_async_callback(self, setup):
        _, monitor, _ = setup
        called = []

        async def async_cb():
            called.append(True)

        monitor.add_tick_callback(async_cb)
        await monitor.tick()
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_status_file_content_format(self, setup):
        _, monitor, status_file = setup
        await monitor.tick()
        content = status_file.read_text()
        assert "**Dirty State**: false" in content
        assert "**Pending Tasks**: 0" in content
        assert "**Cron Jobs Active**: 0" in content
