"""Tests for the dreaming trigger."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.daemon.dreaming_trigger import DreamingTrigger, LAST_DREAMING_KEY
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import ConversationMessage, MessageRole, MemoryEntry


class TestDreamingTrigger:
    @pytest.fixture
    def setup(self, tmp_path):
        conv_db = tmp_path / "conv.db"
        conv_store = ConversationStore(conv_db)
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text("# Memory\n")
        pipeline = DreamingPipeline(conv_store, memory_file)

        daemon_db = tmp_path / "daemon.db"
        daemon_store = DaemonStore(daemon_db)

        trigger = DreamingTrigger(
            conversation_store=conv_store,
            dreaming_pipeline=pipeline,
            daemon_store=daemon_store,
            overnight_hour=3,
            idle_threshold=7200,
        )
        return conv_store, pipeline, daemon_store, trigger

    @pytest.mark.asyncio
    async def test_should_not_run_no_messages(self, setup):
        _, _, _, trigger = setup
        with patch("simpleclaw.daemon.dreaming_trigger.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 19, 4, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await trigger.should_run()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_not_run_before_overnight_hour(self, setup):
        conv_store, _, _, trigger = setup
        # Add a message from 3 hours ago
        old_time = datetime.now() - timedelta(hours=3)
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="test",
            timestamp=old_time,
        ))
        with patch("simpleclaw.daemon.dreaming_trigger.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 19, 2, 0, 0)  # Before 3am
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = await trigger.should_run()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_not_run_recent_input(self, setup):
        conv_store, _, _, trigger = setup
        # Add a recent message (30 min ago)
        recent_time = datetime.now() - timedelta(minutes=30)
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="test",
            timestamp=recent_time,
        ))
        result = await trigger.should_run()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_not_run_already_ran_today(self, setup):
        conv_store, _, daemon_store, trigger = setup
        old_time = datetime.now() - timedelta(hours=3)
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="test",
            timestamp=old_time,
        ))
        # Record that dreaming already ran today
        daemon_store.set_state(LAST_DREAMING_KEY, datetime.now().isoformat())
        result = await trigger.should_run()
        assert result is False

    @pytest.mark.asyncio
    async def test_should_run_conditions_met(self, setup):
        conv_store, _, _, trigger = setup
        # Add a message from 3 hours ago
        old_time = datetime.now() - timedelta(hours=3)
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="test",
            timestamp=old_time,
        ))
        # Set overnight hour to current hour - 1 to ensure we're past it
        trigger._overnight_hour = 0
        result = await trigger.should_run()
        assert result is True

    @pytest.mark.asyncio
    async def test_execute_records_timestamp(self, setup):
        conv_store, _, daemon_store, trigger = setup
        conv_store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="test message",
        ))
        await trigger.execute()
        last = daemon_store.get_state(LAST_DREAMING_KEY)
        assert last is not None
