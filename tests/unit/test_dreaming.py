"""Tests for the dreaming pipeline."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

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
        user_file = tmp_path / "USER.md"
        user_file.write_text("# User Profile\n\n## Preferences\n- Language: Korean\n")
        pipeline = DreamingPipeline(store, memory_file, user_file=user_file)
        return store, pipeline, memory_file, user_file

    def test_create_backup(self, setup):
        _, pipeline, memory_file, _ = setup
        backup = pipeline.create_backup(memory_file)
        assert backup is not None
        assert backup.exists()
        assert backup.read_text() == memory_file.read_text()

    def test_create_backup_no_file(self, tmp_path):
        store = ConversationStore(tmp_path / "test.db")
        pipeline = DreamingPipeline(store, tmp_path / "nonexistent.md")
        assert pipeline.create_backup(tmp_path / "nonexistent.md") is None

    @pytest.mark.asyncio
    async def test_summarize_fallback(self, setup):
        """Without LLM router, fallback summary is used."""
        _, pipeline, _, _ = setup
        messages = [
            ConversationMessage(role=MessageRole.USER, content="What is the weather?"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="It is sunny."),
        ]
        result = await pipeline.summarize(messages)
        assert "memory" in result
        assert len(result["memory"]) > 0

    @pytest.mark.asyncio
    async def test_summarize_empty(self, setup):
        _, pipeline, _, _ = setup
        result = await pipeline.summarize([])
        assert result["memory"] == ""
        assert result["user_insights"] == ""

    def test_append_to_memory(self, setup):
        _, pipeline, memory_file, _ = setup
        pipeline.append_to_memory("## New Summary\n\n- Item 1")
        content = memory_file.read_text()
        assert "Existing content" in content
        assert "New Summary" in content

    def test_update_user_file(self, setup):
        _, pipeline, _, user_file = setup
        pipeline.update_user_file("- Likes KBO baseball")
        content = user_file.read_text()
        assert "Language: Korean" in content
        assert "Likes KBO baseball" in content
        assert "Dreaming Insights" in content

    def test_update_user_file_empty(self, setup):
        _, pipeline, _, user_file = setup
        original = user_file.read_text()
        pipeline.update_user_file("")
        assert user_file.read_text() == original

    @pytest.mark.asyncio
    async def test_run_full_pipeline_fallback(self, setup):
        """Full pipeline without LLM (fallback)."""
        store, pipeline, memory_file, _ = setup
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
        assert "Plan my" in content or "Here is" in content

    @pytest.mark.asyncio
    async def test_run_no_messages(self, setup):
        _, pipeline, _, _ = setup
        result = await pipeline.run()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_with_llm(self, setup):
        """Full pipeline with mocked LLM."""
        store, pipeline, memory_file, user_file = setup

        # Mock LLM router
        mock_response = MagicMock()
        mock_response.text = '{"memory": "## 2026-04-24\\n- Planned the day\\n- Checked weather", "user_insights": "- Interested in daily planning"}'
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Plan my day"
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content="Here is your schedule"
        ))

        result = await pipeline.run()
        assert result is not None

        # MEMORY.md updated
        memory_content = memory_file.read_text()
        assert "Planned the day" in memory_content

        # USER.md updated
        user_content = user_file.read_text()
        assert "daily planning" in user_content

    @pytest.mark.asyncio
    async def test_llm_model_routing(self, setup):
        """Dreaming model is passed to LLM request."""
        store, pipeline, _, _ = setup
        pipeline._dreaming_model = "gemini"

        mock_response = MagicMock()
        mock_response.text = '{"memory": "## test", "user_insights": ""}'
        mock_router = MagicMock()
        mock_router.send = AsyncMock(return_value=mock_response)
        pipeline._router = mock_router

        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="Hello"
        ))

        await pipeline.run()

        # Verify the request used the dreaming model
        call_args = mock_router.send.call_args[0][0]
        assert call_args.backend_name == "gemini"

    def test_parse_llm_result_valid(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_llm_result(
            '{"memory": "## 2026-04-24\\n- item", "user_insights": "- new info"}'
        )
        assert "item" in result["memory"]
        assert "new info" in result["user_insights"]

    def test_parse_llm_result_code_block(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_llm_result(
            '```json\n{"memory": "test", "user_insights": ""}\n```'
        )
        assert result["memory"] == "test"

    def test_parse_llm_result_invalid(self, setup):
        _, pipeline, _, _ = setup
        result = pipeline._parse_llm_result("not json at all")
        assert "memory" in result
        assert result["memory"] == "not json at all"

    @pytest.mark.asyncio
    async def test_backup_both_files(self, setup):
        """Both MEMORY.md and USER.md are backed up."""
        store, pipeline, memory_file, user_file = setup
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="test"
        ))

        await pipeline.run()

        # Check backups exist in memory-backup/ subdirectory
        backup_dir = memory_file.parent / "memory-backup"
        memory_baks = list(backup_dir.glob("MEMORY.*.bak"))
        user_baks = list(backup_dir.glob("USER.*.bak"))
        assert len(memory_baks) >= 1
        assert len(user_baks) >= 1
