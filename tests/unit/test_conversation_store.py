"""Tests for the conversation store."""

import warnings
from datetime import datetime, timedelta

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


class TestConversationStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ConversationStore(tmp_path / "test.db")

    def test_context_manager_closes_without_resource_warning(self, tmp_path):
        db_path = tmp_path / "context-manager.db"
        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always", ResourceWarning)
            with ConversationStore(db_path) as store:
                store.add_message(ConversationMessage(
                    role=MessageRole.USER,
                    content="Hello context manager",
                ))
                assert store.get_recent(limit=1)[0].content == "Hello context manager"
            store.close()

        resource_warnings = [
            warning
            for warning in captured_warnings
            if issubclass(warning.category, ResourceWarning)
        ]
        assert resource_warnings == []

    def test_add_and_retrieve(self, store):
        msg = ConversationMessage(
            role=MessageRole.USER,
            content="Hello agent",
            token_count=3,
        )
        store.add_message(msg)
        messages = store.get_recent(limit=10)
        assert len(messages) == 1
        assert messages[0].content == "Hello agent"
        assert messages[0].role == MessageRole.USER

    def test_recent_order(self, store):
        for i in range(5):
            store.add_message(ConversationMessage(
                role=MessageRole.USER, content=f"Message {i}"
            ))
        recent = store.get_recent(limit=3)
        assert len(recent) == 3
        assert recent[0].content == "Message 2"
        assert recent[2].content == "Message 4"

    def test_count(self, store):
        assert store.count() == 0
        store.add_message(ConversationMessage(
            role=MessageRole.USER, content="test"
        ))
        assert store.count() == 1

    def test_get_since(self, store):
        old_time = datetime.now() - timedelta(hours=1)
        store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="old message",
            timestamp=old_time,
        ))
        store.add_message(ConversationMessage(
            role=MessageRole.USER,
            content="new message",
        ))
        recent = store.get_since(datetime.now() - timedelta(minutes=5))
        assert len(recent) == 1
        assert recent[0].content == "new message"

    def test_empty_store(self, store):
        assert store.get_recent() == []
        assert store.count() == 0
