from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.clarify import encode_callback_data, normalize_options
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.graph_runtime.adapters.delivery import (
    SendNotStartedError,
    SenderReceipt,
)
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
async def test_sqlite_write_lock_does_not_block_telegram_event_loop(
    tmp_path,
) -> None:
    database_path = tmp_path / "conversation.db"
    store = ConversationStore(database_path)
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
        persistence_max_attempts=1,
    )
    lock_ready = threading.Event()
    release_lock = threading.Event()

    def hold_write_lock() -> None:
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            lock_ready.set()
            if not release_lock.wait(timeout=2):
                raise TimeoutError("test did not release SQLite write lock")
            connection.commit()
        finally:
            connection.close()

    lock_task = asyncio.create_task(asyncio.to_thread(hold_write_lock))
    assert await asyncio.to_thread(lock_ready.wait, 1)

    sender = AsyncMock(
        return_value=SenderReceipt(external_message_id="telegram-message-777")
    )
    loop = asyncio.get_running_loop()
    heartbeat_times: list[float] = []
    heartbeat_done = asyncio.Event()

    async def heartbeat() -> None:
        while not heartbeat_done.is_set():
            heartbeat_times.append(loop.time())
            await asyncio.sleep(0.01)

    heartbeat_task = asyncio.create_task(heartbeat())
    delivery_task = asyncio.create_task(
        coordinator.deliver_telegram(
            response,
            destination_ref="42",
            sender=sender,
        )
    )
    try:
        await asyncio.sleep(0.15)
        assert delivery_task.done() is False
        assert len(heartbeat_times) >= 8
        assert max(
            later - earlier
            for earlier, later in zip(heartbeat_times, heartbeat_times[1:])
        ) < 0.06
    finally:
        release_lock.set()

    outcome = await asyncio.wait_for(delivery_task, timeout=1)
    replay = await coordinator.deliver_telegram(
        response,
        destination_ref="42",
        sender=sender,
    )
    heartbeat_done.set()
    await heartbeat_task
    await lock_task

    assert outcome.complete_success is True
    assert replay.complete_success is True
    assert sender.await_count == 1
    assert [
        (message.role, message.turn_id)
        for message in store.get_recent(session_key=response.metadata.session_key)
    ] == [
        (MessageRole.USER, response.metadata.request_id),
        (MessageRole.ASSISTANT, response.metadata.request_id),
    ]
    with sqlite3.connect(database_path) as connection:
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM graph_outbound_persistence"
        ).fetchone()[0]
    assert marker_count == 1


@pytest.mark.asyncio
@pytest.mark.offline
async def test_send_success_persistence_failure_retries_without_replay(
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
        persistence_retry_interval=0,
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

    repaired = await bot._send_response(update, response, chat_id=42, user_id=1)

    assert repaired.delivery_receipt.status is DeliveryStatus.DELIVERED
    assert repaired.persistence_status is PrimaryPersistenceStatus.PERSISTED
    assert repaired.complete_success is True
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
async def test_single_polling_update_retries_persistence_without_resending(
    tmp_path,
    monkeypatch,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    response = _response()
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
        persistence_retry_interval=0,
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

    async def handler(_text, _user_id, _chat_id, *, request_id, **_kwargs):
        store.save_inbound_once(
            ConversationMessage(
                role=MessageRole.USER,
                content="production-shaped request",
                channel="telegram",
            ),
            session_key=response.metadata.session_key,
            request_id=request_id,
        )
        return response

    async def deliver(primary_response, destination_ref, sender):
        return await coordinator.deliver_telegram(
            primary_response,
            destination_ref=destination_ref,
            sender=sender,
        )

    class FakeUpdater:
        async def start_polling(self):
            return None

    class FakeApplication:
        def __init__(self):
            self.handlers = []
            self.updater = FakeUpdater()

        def add_handler(self, handler):
            self.handlers.append(handler)

        async def initialize(self):
            return None

        async def start(self):
            return None

    application = FakeApplication()

    class FakeApplicationBuilder:
        def token(self, _token):
            return self

        def build(self):
            return application

    monkeypatch.setattr(
        "telegram.ext.ApplicationBuilder",
        FakeApplicationBuilder,
    )
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(
        message=SimpleNamespace(
            text="production-shaped request",
            caption=None,
            from_user=SimpleNamespace(id=1),
            chat_id=42,
            message_id=1001,
            message_thread_id=None,
            photo=[],
            document=None,
            reply_text=reply_text,
        )
    )
    bot = TelegramBot(
        "token",
        whitelist_user_ids=[1],
        message_handler=handler,
        primary_delivery_handler=deliver,
    )

    await bot.start()
    message_handler = application.handlers[0]
    await message_handler.callback(update, SimpleNamespace(bot=SimpleNamespace()))

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
async def test_exhausted_persistence_retry_is_not_a_normal_channel_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
        persistence_max_attempts=2,
        persistence_retry_interval=0,
    )
    persistence_attempts = 0

    def fail_persistence(*_args, **_kwargs):
        nonlocal persistence_attempts
        persistence_attempts += 1
        raise RuntimeError("persistent storage outage")

    monkeypatch.setattr(store, "save_outbound_once", fail_persistence)
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)

    with pytest.raises(RuntimeError, match="assistant persistence did not"):
        await bot._send_response(update, _response(), chat_id=42, user_id=1)

    assert reply_text.await_count == 1
    assert persistence_attempts == 2
    assert store.get_recent(session_key="telegram-session-1") == []


@pytest.mark.asyncio
@pytest.mark.offline
async def test_exhausted_persistence_replay_is_typed_and_does_not_resend(
    tmp_path,
    monkeypatch,
) -> None:
    store = ConversationStore(tmp_path / "conversation.db")
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
        persistence_max_attempts=2,
        persistence_retry_interval=0,
    )
    persistence_attempts = 0

    def fail_persistence(*_args, **_kwargs):
        nonlocal persistence_attempts
        persistence_attempts += 1
        raise RuntimeError("persistent storage outage")

    monkeypatch.setattr(store, "save_outbound_once", fail_persistence)
    sender = AsyncMock(
        return_value=SenderReceipt(external_message_id="telegram-message-777")
    )

    first = await coordinator.deliver_telegram(
        _response(),
        destination_ref="42",
        sender=sender,
    )
    replay = await coordinator.deliver_telegram(
        _response(),
        destination_ref="42",
        sender=sender,
    )

    assert first.persistence_status is PrimaryPersistenceStatus.FAILED
    assert replay.persistence_status is PrimaryPersistenceStatus.FAILED
    assert first.persistence_error_type == "RuntimeError"
    assert first.complete_success is False
    assert replay.complete_success is False
    assert sender.await_count == 1
    assert persistence_attempts == 4
    assert store.get_recent(session_key="telegram-session-1") == []


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
