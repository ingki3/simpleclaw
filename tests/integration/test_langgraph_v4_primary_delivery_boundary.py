from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.graph_runtime.adapters.delivery import SendNotStartedError
from simpleclaw.graph_runtime.status import DeliveryStatus
from simpleclaw.memory import ConversationStore
from simpleclaw.outbound_delivery import (
    PrimaryDeliveryCoordinator,
    PrimaryDeliveryMetadataV1,
    PrimaryResponseText,
)


def _response(content: str = "V4 primary answer") -> PrimaryResponseText:
    return PrimaryResponseText(
        content,
        PrimaryDeliveryMetadataV1(
            request_id="telegram:42:1001",
            artifact_id="artifact-1",
            artifact_hash=hashlib.sha256(
                f"content.v1\x1f{content}".encode()
            ).hexdigest(),
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

    first = await bot._send_response(update, response, chat_id=42, user_id=1)
    replay = await bot._send_response(update, response, chat_id=42, user_id=1)

    assert first.delivery_receipt.status is DeliveryStatus.DELIVERED
    assert replay.delivery_receipt.delivery_id == first.delivery_receipt.delivery_id
    assert first.persistence_receipt is not None
    assert replay.persistence_receipt is not None
    assert reply_text.await_count == 1
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
