from __future__ import annotations

import pytest

from simpleclaw.graph_runtime.adapters.delivery import (
    CronDeliveryAdapter,
    NullDeliveryAdapter,
    SendNotStartedError,
    SenderReceipt,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.contracts import DeliveryIntentV1
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    InMemoryDeliveryJournal,
    SQLiteDeliveryJournal,
)
from simpleclaw.graph_runtime.status import DeliveryStatus


def _intent(*, attempts: int = 1) -> DeliveryIntentV1:
    return DeliveryIntentV1(
        delivery_id="delivery-1",
        request_id="request-1",
        artifact_id="artifact-1",
        artifact_hash="artifact-hash",
        channel="telegram",
        destination_ref="chat-1",
        max_attempts=attempts,
    )


@pytest.mark.asyncio
async def test_telegram_delivery_records_external_identity_once() -> None:
    calls = []

    async def sender(destination, content):
        calls.append((destination, content))
        return SenderReceipt(external_message_id="message-1")

    runtime = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )

    first = await runtime.deliver(_intent(), "hello")
    replay = await runtime.deliver(_intent(), "hello")

    assert first.status is DeliveryStatus.DELIVERED
    assert first.external_message_id == "message-1"
    assert replay == first
    assert calls == [("chat-1", "hello")]


@pytest.mark.asyncio
async def test_sender_started_failure_is_unknown_and_never_resent() -> None:
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        raise RuntimeError("connection lost after sender entry")

    journal = InMemoryDeliveryJournal()
    runtime = DeliveryRuntime(
        journal=journal,
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )

    first = await runtime.deliver(_intent(attempts=3), "hello")
    replay = await runtime.deliver(_intent(attempts=3), "hello")

    assert first.status is DeliveryStatus.UNKNOWN
    assert replay == first
    assert calls == 1


@pytest.mark.asyncio
async def test_dispatching_without_receipt_recovers_as_unknown_without_send() -> None:
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        return SenderReceipt(external_message_id="unexpected")

    journal = InMemoryDeliveryJournal()
    journal.record_intent(_intent(), "hello")
    journal.mark_dispatching("delivery-1", attempt=1)
    runtime = DeliveryRuntime(
        journal=journal,
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )

    receipt = await runtime.deliver(_intent(), "hello")

    assert receipt.status is DeliveryStatus.UNKNOWN
    assert calls == 0


@pytest.mark.asyncio
async def test_sqlite_journal_preserves_unknown_boundary_across_process(tmp_path) -> None:
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        return SenderReceipt(external_message_id="unexpected")

    db_path = tmp_path / "delivery.db"
    before_crash = SQLiteDeliveryJournal(db_path)
    before_crash.record_intent(_intent(), "hello")
    before_crash.mark_dispatching("delivery-1", attempt=1)

    recovered = DeliveryRuntime(
        journal=SQLiteDeliveryJournal(db_path),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )
    receipt = await recovered.deliver(_intent(), "hello")

    assert receipt.status is DeliveryStatus.UNKNOWN
    assert calls == 0


@pytest.mark.asyncio
async def test_failed_before_send_retries_delivery_only_with_same_id() -> None:
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SendNotStartedError("preflight refused")
        return SenderReceipt(external_message_id="message-2")

    runtime = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )

    receipt = await runtime.deliver(_intent(attempts=2), "hello")

    assert receipt.status is DeliveryStatus.DELIVERED
    assert receipt.delivery_id == "delivery-1"
    assert receipt.attempt == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_null_and_suppressed_cron_never_call_live_sender() -> None:
    calls = 0

    async def notifier(_destination, _content):
        nonlocal calls
        calls += 1
        return SenderReceipt(external_message_id="cron-1")

    shadow_runtime = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": NullDeliveryAdapter()},
    )
    cron_runtime = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"cron": CronDeliveryAdapter(notifier)},
    )

    shadow = await shadow_runtime.deliver(_intent(), "hello")
    suppressed = await cron_runtime.deliver(
        _intent().model_copy(update={"channel": "cron"}), "[NO_NOTIFY]"
    )

    assert shadow.status is DeliveryStatus.SHADOWED
    assert suppressed.status is DeliveryStatus.SUPPRESSED
    assert calls == 0


@pytest.mark.asyncio
async def test_shadow_intent_never_calls_misconfigured_live_adapter() -> None:
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        return SenderReceipt(external_message_id="must-not-send")

    runtime = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )
    shadow_intent = _intent().model_copy(
        update={"status": DeliveryStatus.SHADOWED}
    )

    receipt = await runtime.deliver(shadow_intent, "hello")

    assert receipt.status is DeliveryStatus.SHADOWED
    assert calls == 0
