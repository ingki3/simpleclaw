"""Tests for the dreaming pipeline."""

from datetime import datetime

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import ConversationMessage, MessageRole


class TestDreamingPipeline:
    @pytest.fixture
    def setup(self, tmp_path):
        db = tmp_path / "test.db"
        store = ConversationStore(db)
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text("# Core Memory\n\nExisting content.\n")
        pipeline = DreamingPipeline(store, memory_file)
        return store, pipeline, memory_file

    def test_create_backup(self, setup):
        _, pipeline, memory_file = setup
        backup = pipeline.create_backup()
        assert backup is not None
        assert backup.exists()
        assert backup.read_text() == memory_file.read_text()

    def test_create_backup_no_file(self, tmp_path):
        store = ConversationStore(tmp_path / "test.db")
        pipeline = DreamingPipeline(store, tmp_path / "nonexistent.md")
        assert pipeline.create_backup() is None

    def test_summarize_messages(self, setup):
        store, pipeline, _ = setup
        messages = [
            ConversationMessage(role=MessageRole.USER, content="What is the weather?"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="It is sunny."),
        ]
        summary = pipeline.summarize(messages)
        assert "Session" in summary
        assert len(summary) > 0

    def test_summarize_empty(self, setup):
        _, pipeline, _ = setup
        assert pipeline.summarize([]) == ""

    def test_append_to_memory(self, setup):
        _, pipeline, memory_file = setup
        pipeline.append_to_memory("## New Summary\n\n- Item 1")
        content = memory_file.read_text()
        assert "Existing content" in content
        assert "New Summary" in content

    @pytest.mark.asyncio
    async def test_run_full_pipeline(self, setup):
        store, pipeline, memory_file = setup
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Plan my day"
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="Here is your schedule"
        ))

        result = await pipeline.run()
        assert result is not None
        assert "dreaming" in result.source

        content = memory_file.read_text()
        assert "Session" in content

    @pytest.mark.asyncio
    async def test_run_no_messages(self, setup):
        _, pipeline, _ = setup
        result = await pipeline.run()
        assert result is None
