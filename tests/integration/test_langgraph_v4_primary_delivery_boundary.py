from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.clarify import encode_callback_data, normalize_options
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.graph_runtime.adapters.delivery import SendNotStartedError
from simpleclaw.graph_runtime.idempotency import (
    IdempotencyInvariantError,
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from simpleclaw.graph_runtime.status import DeliveryStatus
from simpleclaw.memory import ConversationMessage, ConversationStore, MessageRole
from simpleclaw.outbound_delivery import (
    PrimaryDeliveryCoordinator,
    PrimaryDeliveryMetadataV1,
    PrimaryPersistenceStatus,
    PrimaryResponseText,
)


def _response(content: str = "V4 primary answer") -> PrimaryResponseText:
    request_id = "telegram:42:1001"
    return PrimaryResponseText(
        content,
        PrimaryDeliveryMetadataV1(
            request_id=request_id,
            artifact_id=canonical_artifact_id(request_id, content),
            artifact_hash=canonical_artifact_content_hash(content),
            session_key="telegram-session-1",
        ),
    )


def _bot(coordinator: PrimaryDeliveryCoordinator) -> TelegramBot:
    async def deliver(response, destination_ref, sender):
        return await coordinator.deliver_telegram(
            response,
            destination_ref=destination_ref,
            sender=sender,
        )

    return TelegramBot(
        "token",
        whitelist_user_ids=[1],
        primary_delivery_handler=deliver,
    )


@pytest.mark.asyncio
@pytest.mark.offline
async def test_actual_telegram_success_sends_and_persists_once_on_replay(
    tmp_path,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)
    response = _response()
    store.save_inbound_once(
        ConversationMessage(
            role=MessageRole.USER,
            content="production-shaped request",
            channel="telegram",
        ),
        session_key=response.metadata.session_key,
        request_id=response.metadata.request_id,
    )

    first = await bot._send_response(update, response, chat_id=42, user_id=1)
    replay = await bot._send_response(update, response, chat_id=42, user_id=1)

    assert first.delivery_receipt.status is DeliveryStatus.DELIVERED
    assert replay.delivery_receipt.delivery_id == first.delivery_receipt.delivery_id
    assert first.persistence_receipt is not None
    assert replay.persistence_receipt is not None
    assert first.persistence_status is PrimaryPersistenceStatus.PERSISTED
    assert first.complete_success is True
    assert reply_text.await_count == 1
    messages = store.get_recent(session_key="telegram-session-1")
    assert [
        (message.role.value, message.content, message.turn_id)
        for message in messages
    ] == [
        ("user", "production-shaped request", "telegram:42:1001"),
        ("assistant", "V4 primary answer", "telegram:42:1001"),
    ]


@pytest.mark.asyncio
@pytest.mark.offline
async def test_send_success_persistence_failure_is_typed_and_replay_repairs_once(
    tmp_path,
    monkeypatch,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    response = _response()
    store.save_inbound_once(
        ConversationMessage(
            role=MessageRole.USER,
            content="production-shaped request",
            channel="telegram",
        ),
        session_key=response.metadata.session_key,
        request_id=response.metadata.request_id,
    )
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    original_save = store.save_outbound_once
    persistence_attempts = 0

    def fail_first_persistence(*args, **kwargs):
        nonlocal persistence_attempts
        persistence_attempts += 1
        if persistence_attempts == 1:
            raise RuntimeError("injected persistence failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(store, "save_outbound_once", fail_first_persistence)
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)

    failed = await bot._send_response(update, response, chat_id=42, user_id=1)
    replay = await bot._send_response(update, response, chat_id=42, user_id=1)

    assert failed.delivery_receipt.status is DeliveryStatus.DELIVERED
    assert failed.persistence_status is PrimaryPersistenceStatus.FAILED
    assert failed.persistence_error_type == "RuntimeError"
    assert failed.complete_success is False
    assert replay.persistence_status is PrimaryPersistenceStatus.PERSISTED
    assert replay.complete_success is True
    assert reply_text.await_count == 1
    assert persistence_attempts == 2
    assert [
        (message.role, message.turn_id)
        for message in store.get_recent(session_key=response.metadata.session_key)
    ] == [
        (MessageRole.USER, response.metadata.request_id),
        (MessageRole.ASSISTANT, response.metadata.request_id),
    ]


@pytest.mark.asyncio
@pytest.mark.offline
@pytest.mark.parametrize(
    "metadata_update",
    [
        {"artifact_id": "arbitrary-artifact"},
        {
            "request_id": "telegram:42:stale",
            "artifact_id": canonical_artifact_id(
                "telegram:42:1001", "V4 primary answer"
            ),
        },
    ],
)
async def test_actual_telegram_rejects_noncanonical_artifact_before_send(
    tmp_path,
    metadata_update,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    canonical = _response()
    response = PrimaryResponseText(
        str(canonical),
        PrimaryDeliveryMetadataV1(
            request_id=metadata_update.get(
                "request_id", canonical.metadata.request_id
            ),
            artifact_id=metadata_update["artifact_id"],
            artifact_hash=canonical.metadata.artifact_hash,
            session_key=canonical.metadata.session_key,
        ),
    )
    sender = AsyncMock()

    with pytest.raises(IdempotencyInvariantError, match="identity mismatch"):
        await coordinator.deliver_telegram(
            response,
            destination_ref="42",
            sender=sender,
        )

    sender.assert_not_awaited()
    assert store.get_recent(session_key="telegram-session-1") == []


@pytest.mark.asyncio
@pytest.mark.offline
async def test_concurrent_actual_telegram_replay_waits_and_persists_once(
    tmp_path,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
        delivery_lease_seconds=1.0,
        delivery_poll_interval=0.001,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    sends = 0

    async def reply_text(_content):
        nonlocal sends
        sends += 1
        entered.set()
        await release.wait()
        return SimpleNamespace(message_id=777)

    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)
    response = _response()

    owner = asyncio.create_task(
        bot._send_response(update, response, chat_id=42, user_id=1)
    )
    await entered.wait()
    replay = asyncio.create_task(
        bot._send_response(update, response, chat_id=42, user_id=1)
    )
    await asyncio.sleep(0)
    assert not replay.done()

    release.set()
    first, second = await asyncio.gather(owner, replay)

    assert first.delivery_receipt.status is DeliveryStatus.DELIVERED
    assert second.delivery_receipt == first.delivery_receipt
    assert first.persistence_receipt is not None
    assert second.persistence_receipt is not None
    assert sends == 1
    messages = store.get_recent(session_key="telegram-session-1")
    assert [(message.role.value, message.content) for message in messages] == [
        ("assistant", "V4 primary answer")
    ]


@pytest.mark.asyncio
@pytest.mark.offline
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (RuntimeError("ambiguous transport failure"), DeliveryStatus.UNKNOWN),
        (
            SendNotStartedError("telegram preflight failed"),
            DeliveryStatus.FAILED_BEFORE_SEND,
        ),
    ],
)
async def test_actual_telegram_failure_never_persists_or_resends(
    tmp_path,
    error,
    expected_status,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    reply_text = AsyncMock(side_effect=error)
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)
    response = _response()

    first = await bot._send_response(update, response, chat_id=42, user_id=1)
    replay = await bot._send_response(update, response, chat_id=42, user_id=1)

    assert first.delivery_receipt.status is expected_status
    assert replay.delivery_receipt.delivery_id == first.delivery_receipt.delivery_id
    assert first.persistence_receipt is None
    assert replay.persistence_receipt is None
    assert reply_text.await_count == 1
    assert store.get_recent(session_key="telegram-session-1") == []


@pytest.mark.asyncio
@pytest.mark.offline
async def test_sampled_canary_streaming_replay_uses_durable_delivery_once(
    tmp_path,
) -> None:
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator._unified_turn_planner_config = {
        "architecture": "langgraph_v4",
        "mode": "read_only_canary",
        "sample_rate": 1.0,
    }
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )

    async def deliver(response, destination_ref, sender):
        return await coordinator.deliver_telegram(
            response,
            destination_ref=destination_ref,
            sender=sender,
        )

    bot = TelegramBot(
        "token",
        whitelist_user_ids=[1],
        streaming_config={"enabled": True},
        primary_delivery_handler=deliver,
        deferred_delivery_required=orchestrator.deferred_primary_delivery_required,
    )
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    response = _response()

    assert orchestrator.deferred_primary_delivery_required() is True
    assert bot._streaming_enabled_for_current_rollout() is False
    first = await bot._send_response(update, response, chat_id=42, user_id=1)
    replay = await bot._send_response(update, response, chat_id=42, user_id=1)

    assert reply_text.await_count == 1
    assert first.delivery_receipt.delivery_id == replay.delivery_receipt.delivery_id
    messages = store.get_recent(session_key="telegram-session-1")
    assert [(message.role.value, message.content) for message in messages] == [
        ("assistant", "V4 primary answer")
    ]


@pytest.mark.asyncio
@pytest.mark.offline
async def test_clarify_callback_replay_uses_stable_request_delivery_identity(
    tmp_path,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    target_calls = 0
    responses: dict[str, PrimaryResponseText] = {}

    async def handler(text, user_id, chat_id, *, request_id):
        nonlocal target_calls
        if request_id not in responses:
            target_calls += 1
            responses[request_id] = PrimaryResponseText(
                "clarified answer",
                PrimaryDeliveryMetadataV1(
                    request_id=request_id,
                    artifact_id=canonical_artifact_id(
                        request_id, "clarified answer"
                    ),
                    artifact_hash=canonical_artifact_content_hash(
                        "clarified answer"
                    ),
                    session_key="telegram-clarify-session",
                ),
            )
        return responses[request_id]

    async def deliver(response, destination_ref, sender):
        return await coordinator.deliver_telegram(
            response,
            destination_ref=destination_ref,
            sender=sender,
        )

    bot = TelegramBot(
        "token",
        whitelist_user_ids=[1],
        message_handler=handler,
        primary_delivery_handler=deliver,
    )
    bot._cache_clarify_options(42, 1001, normalize_options(["selected option"]))
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=888))
    query = SimpleNamespace(
        id="telegram-callback-id",
        from_user=SimpleNamespace(id=1),
        message=SimpleNamespace(
            chat_id=42,
            message_id=1001,
            message_thread_id=None,
            reply_text=reply_text,
        ),
        data=encode_callback_data(0),
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)

    await bot._on_callback_query(update, SimpleNamespace())
    await bot._on_callback_query(update, SimpleNamespace())

    assert target_calls == 1
    assert reply_text.await_count == 1
    assert list(responses) == [
        "telegram:callback:telegram-callback-id:42:1001:0"
    ]
    messages = store.get_recent(session_key="telegram-clarify-session")
    assert [(message.role.value, message.content) for message in messages] == [
        ("assistant", "clarified answer")
    ]
