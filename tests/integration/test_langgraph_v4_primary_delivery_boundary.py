from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import suppress
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from simpleclaw.agent.clarify import encode_callback_data, normalize_options
from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    DraftResponseV1,
)
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.graph_runtime.adapters.delivery import SendNotStartedError
from simpleclaw.graph_runtime.composition import FinalCompositionRuntime
from simpleclaw.graph_runtime.composition_journal import SQLiteFinalArtifactJournal
from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    ContractRefV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.idempotency import (
    IdempotencyInvariantError,
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    DeliveryStatus,
    EffectStatus,
    TerminalOutcome,
)
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


class _BudgetExhausted(RuntimeError):
    stop_condition = "budget_exhausted"


@pytest.mark.asyncio
@pytest.mark.offline
async def test_central_final_delivery_persistence_and_replay_are_exactly_once(
    tmp_path,
) -> None:
    request_id = "telegram:42:central-1001"
    facts = {
        "data": {
            "category": "KBO",
            "items": [{"rank": 1, "team": "KT", "wins": 59}],
        }
    }
    composition_input = CompositionInputV1(
        request_id=request_id,
        question="KBO 1위 팀과 승수를 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts=facts,
    )
    normalized_result = NormalizedAssetResultV1(
        invocation_id="invocation-1",
        output_contract=ContractRefV1(
            contract_id="recipe.sports-live.output",
            version="1",
            owner_ref=composition_input.asset_ref,
            schema_hash="schema-hash",
        ),
        status=AssetResultStatus.RESOLVED,
        payload={"side_effect": False, **facts},
        payload_hash="payload-hash",
        effect_status=EffectStatus.NONE,
    )
    compose = AsyncMock(
        return_value=DraftResponseV1(
            content="KBO 1위는 KT이며 59승입니다.",
            cited_paths=(
                "data.category",
                "data.items[0].team",
                "data.items[0].wins",
            ),
        )
    )
    journal_path = tmp_path / "graph.db"

    def runtime() -> FinalCompositionRuntime:
        return FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=lambda: "generic fallback",
            journal=SQLiteFinalArtifactJournal(journal_path),
            composer_fingerprint="composer-v1",
        )

    first = await runtime().finalize(
        request_id=request_id,
        normalized_result=normalized_result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=composition_input,
    )
    replay = await runtime().finalize(
        request_id=request_id,
        normalized_result=normalized_result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=composition_input,
    )
    assert first is not None
    assert replay == first

    store = ConversationStore(tmp_path / "conversation.db")
    session_key = "telegram-session-central"
    store.save_inbound_once(
        ConversationMessage(
            role=MessageRole.USER,
            content="KBO 1위 팀과 승수를 알려줘",
            channel="telegram",
        ),
        session_key=session_key,
        request_id=request_id,
    )
    response = PrimaryResponseText(
        first.content,
        PrimaryDeliveryMetadataV1(
            request_id=request_id,
            artifact_id=first.artifact_id,
            artifact_hash=first.content_hash,
            session_key=session_key,
        ),
    )
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)

    await bot._send_response(update, response, chat_id=42, user_id=1)
    await bot._send_response(update, response, chat_id=42, user_id=1)

    assert compose.await_count == 1
    assert reply_text.await_count == 1
    assert [
        (message.role, message.turn_id)
        for message in store.get_recent(session_key=session_key)
    ] == [
        (MessageRole.USER, request_id),
        (MessageRole.ASSISTANT, request_id),
    ]
    with sqlite3.connect(journal_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts"
        ).fetchone()[0] == 1
    with sqlite3.connect(tmp_path / "conversation.db") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_outbound_persistence"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.offline
async def test_budget_fallback_delivery_and_persistence_are_exactly_once(
    tmp_path,
) -> None:
    request_id = "telegram:42:budget-fallback-1001"
    facts = {
        "data": {
            "category": "KBO",
            "items": [{"rank": 1, "team": "KT", "wins": 59}],
        }
    }
    composition_input = CompositionInputV1(
        request_id=request_id,
        question="KBO 1위 팀과 승수를 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="budget-fallback-payload-hash",
        public_facts=facts,
    )
    normalized_result = NormalizedAssetResultV1(
        invocation_id="budget-fallback-invocation",
        output_contract=ContractRefV1(
            contract_id="recipe.sports-live.output",
            version="1",
            owner_ref=composition_input.asset_ref,
            schema_hash="schema-hash",
        ),
        status=AssetResultStatus.RESOLVED,
        payload={"side_effect": False, **facts},
        payload_hash="budget-fallback-payload-hash",
        effect_status=EffectStatus.NONE,
    )
    compose = AsyncMock(
        side_effect=_BudgetExhausted(
            "provider raw diagnostic: KBO 1위 KT 59승"
        )
    )
    safe_render = Mock(
        return_value="요청을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."
    )
    final_journal_path = tmp_path / "graph.db"

    def runtime() -> FinalCompositionRuntime:
        return FinalCompositionRuntime(
            compose=compose,
            guard=guard_final_response,
            safe_render=safe_render,
            journal=SQLiteFinalArtifactJournal(final_journal_path),
            composer_fingerprint="composer-v1",
        )

    first = await runtime().finalize(
        request_id=request_id,
        normalized_result=normalized_result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=composition_input,
    )
    replay = await runtime().finalize(
        request_id=request_id,
        normalized_result=normalized_result,
        outcome=TerminalOutcome.COMPLETED,
        composition_input=composition_input,
    )
    assert first is not None
    assert replay == first

    conversation_path = tmp_path / "conversation.db"
    store = ConversationStore(conversation_path)
    session_key = "telegram-session-budget-fallback"
    store.save_inbound_once(
        ConversationMessage(
            role=MessageRole.USER,
            content="KBO 1위 팀과 승수를 알려줘",
            channel="telegram",
        ),
        session_key=session_key,
        request_id=request_id,
    )
    response = PrimaryResponseText(
        first.content,
        PrimaryDeliveryMetadataV1(
            request_id=request_id,
            artifact_id=first.artifact_id,
            artifact_hash=first.content_hash,
            session_key=session_key,
        ),
    )
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
    )
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=778))
    bot = _bot(coordinator)
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))

    await bot._send_response(update, response, chat_id=42, user_id=1)
    await bot._send_response(update, response, chat_id=42, user_id=1)

    assert compose.await_count == 1
    assert safe_render.call_count == 1
    assert reply_text.await_count == 1
    assert [
        (message.role, message.turn_id)
        for message in store.get_recent(session_key=session_key)
    ] == [
        (MessageRole.USER, request_id),
        (MessageRole.ASSISTANT, request_id),
    ]
    with sqlite3.connect(final_journal_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_artifacts WHERE request_id = ?",
            (request_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_final_composition_claims "
            "WHERE request_id = ?",
            (request_id,),
        ).fetchone()[0] == 0
    with sqlite3.connect(conversation_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_outbound_persistence"
        ).fetchone()[0] == 1


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
async def test_sqlite_write_lock_keeps_event_loop_responsive_and_replays_once(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "conversation.db"
    store = ConversationStore(database)
    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "delivery.db",
        conversation_store=store,
        persistence_retry_interval=0.001,
    )
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
    outcomes = []

    async def handler(_text, _user_id, _chat_id, **_kwargs):
        return response

    async def deliver(primary_response, destination_ref, sender):
        outcome = await coordinator.deliver_telegram(
            primary_response,
            destination_ref=destination_ref,
            sender=sender,
        )
        outcomes.append(outcome)
        return outcome

    class FakeUpdater:
        async def start_polling(self):
            return None

    class FakeApplication:
        def __init__(self):
            self.handlers = []
            self.updater = FakeUpdater()

        def add_handler(self, registered_handler):
            self.handlers.append(registered_handler)

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
    bot = TelegramBot(
        "token",
        whitelist_user_ids=[1],
        message_handler=handler,
        primary_delivery_handler=deliver,
    )
    await bot.start()
    polling_callback = application.handlers[0].callback
    heartbeat_times: list[float] = []
    heartbeat_reached = asyncio.Event()

    async def heartbeat() -> None:
        loop = asyncio.get_running_loop()
        while True:
            heartbeat_times.append(loop.time())
            if len(heartbeat_times) >= 5:
                heartbeat_reached.set()
            await asyncio.sleep(0.01)

    lock = sqlite3.connect(database)
    lock.execute("BEGIN IMMEDIATE")
    heartbeat_task = asyncio.create_task(heartbeat())
    delivery_task = asyncio.create_task(
        polling_callback(update, SimpleNamespace(bot=SimpleNamespace()))
    )
    try:
        await asyncio.wait_for(heartbeat_reached.wait(), timeout=1)
        assert not delivery_task.done()
        lock.commit()
        await asyncio.wait_for(delivery_task, timeout=2)
    finally:
        with suppress(sqlite3.Error):
            lock.rollback()
        lock.close()
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task

    await polling_callback(update, SimpleNamespace(bot=SimpleNamespace()))

    assert [outcome.persistence_status for outcome in outcomes] == [
        PrimaryPersistenceStatus.PERSISTED,
        PrimaryPersistenceStatus.PERSISTED,
    ]
    assert reply_text.await_count == 1
    assert max(
        later - earlier
        for earlier, later in pairwise(heartbeat_times)
    ) < 0.1
    messages = store.get_recent(session_key=response.metadata.session_key)
    assert [
        (message.role, message.turn_id) for message in messages
    ] == [
        (MessageRole.USER, response.metadata.request_id),
        (MessageRole.ASSISTANT, response.metadata.request_id),
    ]
    with sqlite3.connect(database) as connection:
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
    heartbeat_ticks = 0

    def fail_persistence(*_args, **_kwargs):
        nonlocal persistence_attempts
        persistence_attempts += 1
        time.sleep(0.05)
        raise RuntimeError("persistent storage outage")

    async def heartbeat() -> None:
        nonlocal heartbeat_ticks
        while True:
            heartbeat_ticks += 1
            await asyncio.sleep(0.01)

    monkeypatch.setattr(store, "save_outbound_once", fail_persistence)
    reply_text = AsyncMock(return_value=SimpleNamespace(message_id=777))
    update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))
    bot = _bot(coordinator)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        with pytest.raises(RuntimeError, match="assistant persistence did not"):
            await bot._send_response(update, _response(), chat_id=42, user_id=1)
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task

    assert reply_text.await_count == 1
    assert persistence_attempts == 2
    assert heartbeat_ticks >= 5
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
