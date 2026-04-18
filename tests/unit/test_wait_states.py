"""Tests for the wait state manager."""

from datetime import datetime, timedelta

import pytest

from simpleclaw.daemon.models import WaitState
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.daemon.wait_states import WaitStateManager


class TestWaitStateManager:
    @pytest.fixture
    def setup(self, tmp_path):
        store = DaemonStore(tmp_path / "test.db")
        manager = WaitStateManager(store, default_timeout=3600)
        return store, manager

    def test_register_wait(self, setup):
        _, manager = setup
        wait = manager.register_wait(
            task_id="task-1",
            state={"step": 3, "context": "waiting"},
            condition_type="callback",
        )
        assert wait.task_id == "task-1"
        assert wait.condition_type == "callback"
        assert wait.timeout_seconds == 3600

    def test_register_wait_custom_timeout(self, setup):
        _, manager = setup
        wait = manager.register_wait(
            task_id="task-2",
            state={},
            condition_type="timer",
            timeout=600,
        )
        assert wait.timeout_seconds == 600

    def test_resolve_wait(self, setup):
        _, manager = setup
        manager.register_wait("task-1", {}, "callback")
        resolved = manager.resolve_wait("task-1", "completed")
        assert resolved is not None
        assert resolved.resolution == "completed"
        assert resolved.resolved_at is not None

    def test_resolve_nonexistent(self, setup):
        _, manager = setup
        assert manager.resolve_wait("nonexistent") is None

    def test_get_pending(self, setup):
        _, manager = setup
        manager.register_wait("task-1", {}, "callback")
        manager.register_wait("task-2", {}, "timer")
        pending = manager.get_pending()
        assert len(pending) == 2

    def test_get_pending_excludes_resolved(self, setup):
        _, manager = setup
        manager.register_wait("task-1", {}, "callback")
        manager.register_wait("task-2", {}, "timer")
        manager.resolve_wait("task-1")
        pending = manager.get_pending()
        assert len(pending) == 1
        assert pending[0].task_id == "task-2"

    def test_check_timeouts(self, setup):
        store, manager = setup
        # Create a wait state that's already timed out
        old_time = datetime.now() - timedelta(hours=2)
        wait = WaitState(
            task_id="expired",
            serialized_state="{}",
            condition_type="callback",
            registered_at=old_time,
            timeout_seconds=60,
        )
        store.save_wait_state(wait)
        timed_out = manager.check_timeouts()
        assert len(timed_out) == 1
        assert timed_out[0].task_id == "expired"

    def test_check_timeouts_skips_active(self, setup):
        _, manager = setup
        manager.register_wait("still-active", {}, "callback", timeout=9999)
        timed_out = manager.check_timeouts()
        assert len(timed_out) == 0

    def test_get_state_data(self, setup):
        _, manager = setup
        manager.register_wait("task-1", {"step": 3, "key": "value"}, "callback")
        data = manager.get_state_data("task-1")
        assert data == {"step": 3, "key": "value"}
        assert manager.get_state_data("nonexistent") is None
