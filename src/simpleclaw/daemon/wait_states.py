"""Wait state manager: serialize/resume paused tasks."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from simpleclaw.daemon.models import WaitState, WaitStateNotFoundError
from simpleclaw.daemon.store import DaemonStore

logger = logging.getLogger(__name__)


class WaitStateManager:
    """Manages async wait states for paused tasks."""

    def __init__(self, store: DaemonStore, default_timeout: int = 3600) -> None:
        self._store = store
        self._default_timeout = default_timeout

    def register_wait(
        self,
        task_id: str,
        state: dict,
        condition_type: str,
        timeout: int | None = None,
    ) -> WaitState:
        """Register a new wait state for a paused task."""
        wait = WaitState(
            task_id=task_id,
            serialized_state=json.dumps(state),
            condition_type=condition_type,
            registered_at=datetime.now(),
            timeout_seconds=timeout or self._default_timeout,
        )
        self._store.save_wait_state(wait)
        logger.info(
            "Registered wait state: task_id=%s, type=%s, timeout=%ds",
            task_id,
            condition_type,
            wait.timeout_seconds,
        )
        return wait

    def resolve_wait(
        self, task_id: str, resolution: str = "completed"
    ) -> WaitState | None:
        """Resolve a wait state when its condition is met."""
        wait = self._store.get_wait_state(task_id)
        if wait is None:
            return None
        if wait.resolved_at is not None:
            return wait  # Already resolved

        self._store.resolve_wait_state(task_id, resolution)
        # Re-fetch to get updated fields
        updated = self._store.get_wait_state(task_id)
        logger.info(
            "Resolved wait state: task_id=%s, resolution=%s",
            task_id,
            resolution,
        )
        return updated

    def get_pending(self) -> list[WaitState]:
        """Get all unresolved wait states."""
        return self._store.get_pending_waits()

    def check_timeouts(self) -> list[WaitState]:
        """Check for timed-out wait states and resolve them.

        Returns list of wait states that were timed out.
        """
        now = datetime.now()
        pending = self._store.get_pending_waits()
        timed_out = []

        for wait in pending:
            elapsed = (now - wait.registered_at).total_seconds()
            if elapsed >= wait.timeout_seconds:
                self._store.resolve_wait_state(wait.task_id, "timeout")
                timed_out.append(wait)
                logger.warning(
                    "Wait state timed out: task_id=%s (elapsed=%.0fs, limit=%ds)",
                    wait.task_id,
                    elapsed,
                    wait.timeout_seconds,
                )

        return timed_out

    def get_state_data(self, task_id: str) -> dict | None:
        """Get the deserialized state data for a wait state."""
        wait = self._store.get_wait_state(task_id)
        if wait is None:
            return None
        try:
            return json.loads(wait.serialized_state)
        except json.JSONDecodeError:
            return None
