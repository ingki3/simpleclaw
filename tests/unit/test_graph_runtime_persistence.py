from __future__ import annotations

import hashlib
import threading
from unittest.mock import AsyncMock

import pytest

from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.graph_runtime.adapters.delivery import SenderReceipt
from simpleclaw.memory import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.outbound_delivery import (
    PrimaryDeliveryCoordinator,
    PrimaryDeliveryMetadataV1,
    PrimaryPersistenceStatus,
    PrimaryResponseText,
)
from simpleclaw.graph_runtime.idempotency import (
    canonical_artifact_content_hash,
    canonical_artifact_id,
)


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


@pytest.mark.asyncio
async def test_primary_delivery_runs_all_conversation_store_calls_off_loop(
    tmp_path,
    monkeypatch,
) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    loop_thread_id = threading.get_ident()
    call_threads: dict[str, list[int]] = {
        "get": [],
        "save": [],
        "bind": [],
    }

    for method_name, key in (
        ("get_outbound_persistence", "get"),
        ("save_outbound_once", "save"),
        ("bind_outbound_to_turn", "bind"),
    ):
        original = getattr(store, method_name)

        def record_thread(*args, _original=original, _key=key, **kwargs):
            call_threads[_key].append(threading.get_ident())
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, method_name, record_thread)

    content = "delivered response"
    request_id = "telegram:42:1001"
    response = PrimaryResponseText(
        content,
        PrimaryDeliveryMetadataV1(
            request_id=request_id,
            artifact_id=canonical_artifact_id(request_id, content),
            artifact_hash=canonical_artifact_content_hash(content),
            session_key="session-1",
        ),
    )
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    sender = AsyncMock(return_value=SenderReceipt("telegram-message-1"))

    first = await coordinator.deliver_telegram(
        response,
        destination_ref="42",
        sender=sender,
    )
    replay = await coordinator.deliver_telegram(
        response,
        destination_ref="42",
        sender=sender,
    )

    assert first.persistence_status is PrimaryPersistenceStatus.PERSISTED
    assert replay.persistence_status is PrimaryPersistenceStatus.PERSISTED
    assert sender.await_count == 1
    assert all(call_threads.values())
    assert all(
        thread_id != loop_thread_id
        for thread_ids in call_threads.values()
        for thread_id in thread_ids
    )
