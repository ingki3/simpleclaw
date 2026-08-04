from __future__ import annotations

import pytest

from simpleclaw.graph_runtime.adapters.delivery import (
    SenderReceipt,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.composition import FinalCompositionRuntime
from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    ContractRefV1,
    DeliveryIntentV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    InMemoryDeliveryJournal,
    InMemoryPersistenceJournal,
    PersistenceRuntime,
)
from simpleclaw.graph_runtime.status import (
    AssetResultStatus,
    DeliveryStatus,
    TerminalOutcome,
)


def _result() -> NormalizedAssetResultV1:
    owner = AssetRefV1(type="skill", name="generic")
    return NormalizedAssetResultV1(
        invocation_id="invocation-1",
        output_contract=ContractRefV1(
            contract_id="output",
            version="1",
            owner_ref=owner,
            schema_hash="schema-hash",
        ),
        status=AssetResultStatus.RESOLVED,
        payload={"answer": "verified"},
        payload_hash="payload-hash",
    )


@pytest.mark.asyncio
async def test_guard_failure_uses_safe_renderer_once_without_redispatch() -> None:
    calls = {"compose": 0, "guard": 0, "safe": 0, "dispatch": 0}

    async def compose(_result):
        calls["compose"] += 1
        return "unsafe draft"

    async def guard(_content):
        calls["guard"] += 1
        return False

    def safe(_result):
        calls["safe"] += 1
        return "안전하게 응답을 제공할 수 없습니다."

    runtime = FinalCompositionRuntime(compose=compose, guard=guard, safe_render=safe)
    final = await runtime.finalize(
        request_id="request-1",
        normalized_result=_result(),
        outcome=TerminalOutcome.COMPLETED,
    )

    assert final is not None
    assert final.content == "안전하게 응답을 제공할 수 없습니다."
    assert calls == {"compose": 1, "guard": 1, "safe": 1, "dispatch": 0}


@pytest.mark.asyncio
async def test_delivered_persistence_crash_recovers_without_resend() -> None:
    sends = 0
    rows: dict[str, str] = {}
    fail_after_write = True

    async def sender(_destination, _content):
        nonlocal sends
        sends += 1
        return SenderReceipt(external_message_id="message-1")

    async def persist(persistence_id, payload_hash, content):
        nonlocal fail_after_write
        existing = rows.get(persistence_id)
        if existing is not None and existing != payload_hash:
            raise ValueError("conflicting persistence payload")
        rows[persistence_id] = payload_hash
        if fail_after_write:
            fail_after_write = False
            raise RuntimeError("crash after durable store write")

    intent = DeliveryIntentV1(
        delivery_id="delivery-1",
        request_id="request-1",
        artifact_id="artifact-1",
        artifact_hash="artifact-hash",
        channel="telegram",
        destination_ref="chat-1",
    )
    delivery = DeliveryRuntime(
        journal=InMemoryDeliveryJournal(),
        adapters={"telegram": TelegramDeliveryAdapter(sender)},
    )
    receipt = await delivery.deliver(intent, "hello")
    persistence = PersistenceRuntime(
        journal=InMemoryPersistenceJournal(), writer=persist
    )

    with pytest.raises(RuntimeError, match="crash"):
        await persistence.persist_delivered(
            session_key="session-1",
            request_id="request-1",
            artifact_hash="artifact-hash",
            content="hello",
            delivery_receipt=receipt,
        )
    recovered = await persistence.persist_delivered(
        session_key="session-1",
        request_id="request-1",
        artifact_hash="artifact-hash",
        content="hello",
        delivery_receipt=receipt,
    )

    assert recovered.persisted is True
    assert sends == 1
    assert len(rows) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        DeliveryStatus.SHADOWED,
        DeliveryStatus.SUPPRESSED,
        DeliveryStatus.FAILED_BEFORE_SEND,
        DeliveryStatus.UNKNOWN,
    ],
)
async def test_non_delivered_receipt_never_persists(status) -> None:
    writes = 0

    async def persist(_persistence_id, _payload_hash, _content):
        nonlocal writes
        writes += 1

    persistence = PersistenceRuntime(
        journal=InMemoryPersistenceJournal(), writer=persist
    )
    receipt = persistence.delivery_receipt_for_test(
        delivery_id="delivery-1", status=status
    )

    result = await persistence.persist_delivered(
        session_key="session-1",
        request_id="request-1",
        artifact_hash="artifact-hash",
        content="hello",
        delivery_receipt=receipt,
    )

    assert result is None
    assert writes == 0
