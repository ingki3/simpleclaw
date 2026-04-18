"""Dreaming trigger: condition evaluator for automatic dreaming pipeline."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from simpleclaw.daemon.store import DaemonStore
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline

logger = logging.getLogger(__name__)

LAST_DREAMING_KEY = "last_dreaming_timestamp"


class DreamingTrigger:
    """Evaluates conditions for automatic dreaming and triggers the pipeline.

    Conditions (both must be true):
    1. Last user input was more than `idle_threshold` seconds ago.
    2. Current time has passed the configured `overnight_hour`.

    Additionally, dreaming only runs once per calendar day.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        dreaming_pipeline: DreamingPipeline,
        daemon_store: DaemonStore,
        overnight_hour: int = 3,
        idle_threshold: int = 7200,
    ) -> None:
        self._conv_store = conversation_store
        self._pipeline = dreaming_pipeline
        self._daemon_store = daemon_store
        self._overnight_hour = overnight_hour
        self._idle_threshold = idle_threshold

    async def should_run(self) -> bool:
        """Check if dreaming conditions are met."""
        now = datetime.now()

        # Check 1: Already ran today?
        last_dreaming = self._get_last_dreaming()
        if last_dreaming and last_dreaming.date() == now.date():
            return False

        # Check 2: Past overnight hour?
        if now.hour < self._overnight_hour:
            return False

        # Check 3: No new messages since last dreaming?
        messages = self._conv_store.get_recent(limit=1)
        if not messages:
            return False

        # Check 4: Last user input idle for threshold?
        last_input = messages[-1].timestamp
        if last_input is None:
            return False

        idle_seconds = (now - last_input).total_seconds()
        if idle_seconds < self._idle_threshold:
            return False

        return True

    async def execute(self) -> None:
        """Run the dreaming pipeline and record the timestamp."""
        logger.info("Dreaming trigger: conditions met, starting dreaming pipeline.")

        last_dreaming = self._get_last_dreaming()

        try:
            result = await self._pipeline.run(last_dreaming)
            if result:
                self._daemon_store.set_state(
                    LAST_DREAMING_KEY, datetime.now().isoformat()
                )
                logger.info("Dreaming pipeline completed: %s", result.source)
            else:
                logger.info("Dreaming pipeline: no new content to process.")
        except Exception:
            logger.exception("Dreaming pipeline failed")

    def _get_last_dreaming(self) -> datetime | None:
        """Get the last dreaming timestamp from daemon state."""
        value = self._daemon_store.get_state(LAST_DREAMING_KEY)
        if value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None
