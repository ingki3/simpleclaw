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

    def test_hide_recent_user_turn_excludes_user_assistant_pair_from_context(self, store):
        store.add_message(ConversationMessage(role=MessageRole.USER, content="u1"))
        store.add_message(ConversationMessage(role=MessageRole.ASSISTANT, content="a1"))
        hidden_user_turns = store.hide_recent_user_turns(1)

        assert hidden_user_turns == 1
        assert store.get_recent(limit=10) == []

        audit_messages = store.get_recent(limit=10, include_deleted=True)
        assert [msg.content for msg in audit_messages] == ["u1", "a1"]
        assert store.count() == 2

    def test_hide_recent_two_user_turns_preserves_older_context(self, store):
        for content, role in [
            ("u1", MessageRole.USER),
            ("a1", MessageRole.ASSISTANT),
            ("u2", MessageRole.USER),
            ("a2", MessageRole.ASSISTANT),
            ("u3", MessageRole.USER),
            ("a3", MessageRole.ASSISTANT),
        ]:
            store.add_message(ConversationMessage(role=role, content=content))

        hidden_user_turns = store.hide_recent_user_turns(2)

        assert hidden_user_turns == 2
        assert [msg.content for msg in store.get_recent(limit=10)] == ["u1", "a1"]
        assert [msg.content for msg in store.get_recent(limit=10, include_deleted=True)] == [
            "u1", "a1", "u2", "a2", "u3", "a3",
        ]

    def test_get_since_and_ids_exclude_hidden_by_default_but_allow_audit(self, store):
        since = datetime.now() - timedelta(minutes=1)
        user_id = store.add_message(ConversationMessage(role=MessageRole.USER, content="u1"))
        asst_id = store.add_message(ConversationMessage(role=MessageRole.ASSISTANT, content="a1"))

        assert store.hide_recent_user_turns(1) == 1

        assert store.get_since(since) == []
        assert store.get_since_with_ids(since) == []
        assert store.get_messages_by_ids([user_id, asst_id], include_deleted=False) == []
        assert [
            msg.content
            for _, msg in store.get_messages_by_ids([user_id, asst_id])
        ] == ["u1", "a1"]

    def test_hide_recent_user_turns_returns_zero_without_user_messages(self, store):
        store.add_message(ConversationMessage(role=MessageRole.ASSISTANT, content="a1"))

        assert store.hide_recent_user_turns(1) == 0
        assert [msg.content for msg in store.get_recent(limit=10)] == ["a1"]
