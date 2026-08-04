from __future__ import annotations

import hashlib

import pytest

from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.memory import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


def test_conversation_store_outbound_persistence_is_exactly_once(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    content = "delivered response"
    payload_hash = hashlib.sha256(content.encode()).hexdigest()
    message = ConversationMessage(role=MessageRole.ASSISTANT, content=content)

    first_id, first_created = store.save_outbound_once(
        message,
        session_key="session-1",
        persistence_id="persistence-1",
        payload_hash=payload_hash,
    )
    replay_id, replay_created = store.save_outbound_once(
        message,
        session_key="session-1",
        persistence_id="persistence-1",
        payload_hash=payload_hash,
    )

    assert first_created is True
    assert replay_created is False
    assert replay_id == first_id
    assert [item.content for item in store.get_recent()] == [content]


def test_conversation_store_rejects_persistence_id_payload_conflict(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    adapter = ConversationStorePersistenceAdapter(
        store, session_key="session-1", channel="telegram"
    )
    first = "first"
    adapter("persistence-1", hashlib.sha256(first.encode()).hexdigest(), first)

    second = "second"
    with pytest.raises(ValueError, match="different payload"):
        adapter(
            "persistence-1",
            hashlib.sha256(second.encode()).hexdigest(),
            second,
        )
