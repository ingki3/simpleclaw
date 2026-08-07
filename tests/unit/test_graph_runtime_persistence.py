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
        turn_id="telegram:42:1001",
    )
    replay_id, replay_created = store.save_outbound_once(
        message,
        session_key="session-1",
        persistence_id="persistence-1",
        payload_hash=payload_hash,
        turn_id="telegram:42:1001",
    )

    assert first_created is True
    assert replay_created is False
    assert replay_id == first_id
    assert [
        (item.content, item.turn_id) for item in store.get_recent()
    ] == [(content, "telegram:42:1001")]


def test_conversation_store_rebinds_legacy_outbound_to_request_turn(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    content = "already delivered response"
    payload_hash = hashlib.sha256(content.encode()).hexdigest()
    message_id, _ = store.save_outbound_once(
        ConversationMessage(role=MessageRole.ASSISTANT, content=content),
        session_key="session-1",
        persistence_id="legacy-persistence-id",
        payload_hash=payload_hash,
    )

    rebound_id = store.bind_outbound_to_turn(
        "legacy-persistence-id",
        payload_hash=payload_hash,
        turn_id="telegram:42:1002",
    )

    assert rebound_id == message_id
    assert [
        (item.content, item.turn_id) for item in store.get_recent()
    ] == [(content, "telegram:42:1002")]


def test_conversation_store_rejects_persistence_id_payload_conflict(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    adapter = ConversationStorePersistenceAdapter(
        store, channel="telegram"
    )
    first = "first"
    adapter(
        "session-1",
        "persistence-1",
        hashlib.sha256(first.encode()).hexdigest(),
        first,
    )

    second = "second"
    with pytest.raises(ValueError, match="different payload"):
        adapter(
            "session-1",
            "persistence-1",
            hashlib.sha256(second.encode()).hexdigest(),
            second,
        )
