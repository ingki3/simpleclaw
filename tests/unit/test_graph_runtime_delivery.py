from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from simpleclaw.graph_runtime.adapters.delivery import (
    CronDeliveryAdapter,
    NullDeliveryAdapter,
    SenderReceipt,
    SendNotStartedError,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.contracts import DeliveryIntentV1
from simpleclaw.graph_runtime.events import DeliveryReceiptV1
from simpleclaw.graph_runtime.idempotency import (
    IdempotencyInvariantError,
    canonical_artifact_content_hash,
    canonical_artifact_id,
)
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    InMemoryDeliveryJournal,
    SQLiteDeliveryJournal,
)
from simpleclaw.graph_runtime.status import DeliveryStatus


def _intent(*, attempts: int = 1) -> DeliveryIntentV1:
    request_id = "request-1"
    content = "hello"
    return DeliveryIntentV1(
        delivery_id="delivery-1",
        request_id=request_id,
        artifact_id=canonical_artifact_id(request_id, content),
        artifact_hash=canonical_artifact_content_hash(content),
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
@pytest.mark.parametrize("receipt", [None, SenderReceipt()])
async def test_sender_without_external_identity_is_unknown_and_never_resent(
    receipt,
) -> None:
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        return receipt

    runtime = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )

    first = await runtime.deliver(_intent(attempts=3), "hello")
    replay = await runtime.deliver(_intent(attempts=3), "hello")

    assert first.status is DeliveryStatus.UNKNOWN
    assert first.external_message_id is None
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
    claim = journal.claim_dispatch(
        "delivery-1",
        attempt=1,
        owner_token="crashed-owner",
        lease_expires_at=100.0,
    )
    assert claim is not None
    runtime = DeliveryRuntime(
        journal=journal,
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
        clock=lambda: 101.0,
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
    claim = before_crash.claim_dispatch(
        "delivery-1",
        attempt=1,
        owner_token="crashed-owner",
        lease_expires_at=100.0,
    )
    assert claim is not None

    recovered = DeliveryRuntime(
        journal=SQLiteDeliveryJournal(db_path),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
        clock=lambda: 101.0,
    )
    receipt = await recovered.deliver(_intent(), "hello")

    assert receipt.status is DeliveryStatus.UNKNOWN
    assert calls == 0


@pytest.mark.asyncio
async def test_active_owner_replay_waits_for_same_terminal_receipt() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return SenderReceipt(external_message_id="message-1")

    journal = InMemoryDeliveryJournal()
    runtime = DeliveryRuntime(
        journal=journal,
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
        lease_seconds=1.0,
        poll_interval=0.001,
    )

    owner = asyncio.create_task(runtime.deliver(_intent(), "hello"))
    await entered.wait()
    replay = asyncio.create_task(runtime.deliver(_intent(), "hello"))
    await asyncio.sleep(0)
    assert not replay.done()
    assert journal.get("delivery-1").receipt is None

    release.set()
    owner_receipt, replay_receipt = await asyncio.gather(owner, replay)

    assert owner_receipt.status is DeliveryStatus.DELIVERED
    assert replay_receipt == owner_receipt
    assert calls == 1


@pytest.mark.asyncio
async def test_cancelled_owner_expires_to_unknown_without_automatic_resend(
    tmp_path,
) -> None:
    entered = asyncio.Event()
    never_release = asyncio.Event()
    calls = 0

    async def sender(_destination, _content):
        nonlocal calls
        calls += 1
        entered.set()
        await never_release.wait()
        return SenderReceipt(external_message_id="unexpected")

    db_path = tmp_path / "delivery.db"
    owner_runtime = DeliveryRuntime(
        journal=SQLiteDeliveryJournal(db_path),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
        lease_seconds=0.03,
        poll_interval=0.001,
    )
    owner = asyncio.create_task(owner_runtime.deliver(_intent(), "hello"))
    await entered.wait()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    await asyncio.sleep(0.04)

    replay = await DeliveryRuntime(
        journal=SQLiteDeliveryJournal(db_path),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
        lease_seconds=0.03,
        poll_interval=0.001,
    ).deliver(_intent(), "hello")

    assert replay.status is DeliveryStatus.UNKNOWN
    assert calls == 1


def test_delivery_lease_renewal_requires_current_unexpired_owner() -> None:
    journal = InMemoryDeliveryJournal()
    journal.record_intent(_intent(), "hello")
    claim = journal.claim_dispatch(
        "delivery-1",
        attempt=1,
        owner_token="owner-1",
        lease_expires_at=20.0,
    )
    assert claim is not None

    renewed = journal.renew_dispatch(
        claim, now=15.0, lease_expires_at=30.0
    )

    assert renewed.lease_expires_at == 30.0
    assert journal.get("delivery-1").dispatch_lease_expires_at == 30.0
    with pytest.raises(IdempotencyInvariantError, match="expired"):
        journal.renew_dispatch(renewed, now=31.0, lease_expires_at=40.0)


def test_stale_owner_receipt_is_fenced_and_current_receipt_is_preserved() -> None:
    journal = InMemoryDeliveryJournal()
    journal.record_intent(_intent(), "hello")
    stale = journal.claim_dispatch(
        "delivery-1",
        attempt=1,
        owner_token="owner-1",
        lease_expires_at=20.0,
    )
    assert stale is not None
    journal.clear_failed_before_send(stale)
    current = journal.claim_dispatch(
        "delivery-1",
        attempt=2,
        owner_token="owner-2",
        lease_expires_at=30.0,
    )
    assert current is not None
    stale_receipt = _receipt(
        attempt=1,
        status=DeliveryStatus.UNKNOWN,
        external_message_id=None,
    )
    delivered = _receipt(
        attempt=2,
        status=DeliveryStatus.DELIVERED,
        external_message_id="message-2",
    )

    with pytest.raises(IdempotencyInvariantError, match="fenced"):
        journal.record_receipt(stale_receipt, claim=stale, now=15.0)
    journal.record_receipt(delivered, claim=current, now=25.0)
    with pytest.raises(IdempotencyInvariantError, match="write-once"):
        journal.record_receipt(stale_receipt, claim=stale, now=15.0)

    assert journal.get("delivery-1").receipt == delivered


def test_sqlite_receipt_cas_rejects_foreign_owner(tmp_path) -> None:
    journal = SQLiteDeliveryJournal(tmp_path / "delivery.db")
    journal.record_intent(_intent(), "hello")
    current = journal.claim_dispatch(
        "delivery-1",
        attempt=1,
        owner_token="owner-1",
        lease_expires_at=30.0,
    )
    assert current is not None
    foreign = current.__class__(
        delivery_id=current.delivery_id,
        attempt=current.attempt,
        owner_token="foreign-owner",
        fencing_token=current.fencing_token,
        lease_expires_at=current.lease_expires_at,
    )
    delivered = _receipt(
        attempt=1,
        status=DeliveryStatus.DELIVERED,
        external_message_id="message-1",
    )

    with pytest.raises(IdempotencyInvariantError, match="fenced"):
        journal.record_receipt(delivered, claim=foreign, now=20.0)
    assert journal.get("delivery-1").receipt is None

    journal.record_receipt(delivered, claim=current, now=20.0)
    assert journal.get("delivery-1").receipt == delivered


def _receipt(
    *,
    attempt: int,
    status: DeliveryStatus,
    external_message_id: str | None,
) -> DeliveryReceiptV1:
    return DeliveryReceiptV1(
        receipt_id=f"delivery-1:{attempt}:{status.value}",
        request_id="request-1",
        delivery_id="delivery-1",
        sequence=attempt,
        occurred_at=datetime.now(UTC),
        status=status,
        attempt=attempt,
        external_message_id=external_message_id,
    )


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
