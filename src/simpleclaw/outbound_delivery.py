"""V4 primary final을 실제 channel delivery와 durable persistence에 연결한다."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from simpleclaw.graph_runtime.adapters.delivery import (
    SenderCallback,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.adapters.persistence import (
    ConversationStorePersistenceAdapter,
)
from simpleclaw.graph_runtime.contracts import DeliveryIntentV1
from simpleclaw.graph_runtime.events import DeliveryReceiptV1
from simpleclaw.graph_runtime.idempotency import delivery_id, persistence_id
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    InMemoryPersistenceJournal,
    PersistenceReceiptV1,
    PersistenceRuntime,
    SQLiteDeliveryJournal,
)
from simpleclaw.graph_runtime.status import DeliveryStatus


@dataclass(frozen=True, slots=True)
class PrimaryDeliveryMetadataV1:
    """Graph final artifact와 outer channel이 공유하는 불변 delivery 식별자다."""

    request_id: str
    artifact_id: str
    artifact_hash: str
    session_key: str


class PrimaryResponseText(str):
    """기존 str API를 유지하면서 V4 delivery metadata를 운반한다."""

    metadata: PrimaryDeliveryMetadataV1

    def __new__(
        cls,
        content: str,
        metadata: PrimaryDeliveryMetadataV1,
    ) -> Self:
        value = str.__new__(cls, content)
        value.metadata = metadata
        return value


@dataclass(frozen=True, slots=True)
class PrimaryDeliveryOutcomeV1:
    delivery_receipt: DeliveryReceiptV1
    persistence_receipt: PersistenceReceiptV1 | None


class PrimaryDeliveryCoordinator:
    """Actual Telegram send와 delivered assistant write를 한 identity로 직렬화한다."""

    def __init__(
        self,
        *,
        journal_path: str | Path,
        conversation_store,
        delivery_lease_seconds: float = 30.0,
        delivery_poll_interval: float = 0.01,
    ) -> None:
        self._delivery_journal = SQLiteDeliveryJournal(journal_path)
        self._store = conversation_store
        self._delivery_lease_seconds = delivery_lease_seconds
        self._delivery_poll_interval = delivery_poll_interval

    async def deliver_telegram(
        self,
        response: PrimaryResponseText,
        *,
        destination_ref: str,
        sender: SenderCallback,
    ) -> PrimaryDeliveryOutcomeV1:
        metadata = response.metadata
        expected_hash = hashlib.sha256(
            f"content.v1\x1f{response}".encode()
        ).hexdigest()
        if metadata.artifact_hash != expected_hash:
            raise ValueError("primary response content hash mismatch")
        identity = delivery_id(
            metadata.request_id,
            metadata.artifact_hash,
            destination_ref,
        )
        intent = DeliveryIntentV1(
            delivery_id=identity,
            request_id=metadata.request_id,
            artifact_id=metadata.artifact_id,
            artifact_hash=metadata.artifact_hash,
            channel="telegram",
            destination_ref=destination_ref,
            max_attempts=1,
        )
        receipt = await DeliveryRuntime(
            journal=self._delivery_journal,
            adapters={"telegram": TelegramDeliveryAdapter(sender)},
            lease_seconds=self._delivery_lease_seconds,
            poll_interval=self._delivery_poll_interval,
        ).deliver(intent, str(response))
        if receipt.status is not DeliveryStatus.DELIVERED:
            return PrimaryDeliveryOutcomeV1(receipt, None)

        persistence_identity = persistence_id(
            metadata.session_key,
            metadata.request_id,
            metadata.artifact_hash,
        )
        payload_hash = hashlib.sha256(str(response).encode()).hexdigest()
        if self._store.get_outbound_persistence(
            persistence_identity,
            payload_hash=payload_hash,
        ) is not None:
            persisted = PersistenceReceiptV1(
                persistence_identity,
                payload_hash,
                True,
            )
        else:
            persisted = await PersistenceRuntime(
                journal=InMemoryPersistenceJournal(),
                writer=ConversationStorePersistenceAdapter(
                    self._store,
                    channel="telegram",
                ),
            ).persist_delivered(
                session_key=metadata.session_key,
                request_id=metadata.request_id,
                artifact_hash=metadata.artifact_hash,
                content=str(response),
                delivery_receipt=receipt,
            )
        return PrimaryDeliveryOutcomeV1(receipt, persisted)


PrimaryDeliveryHandler = Callable[
    [PrimaryResponseText, str, SenderCallback],
    Awaitable[PrimaryDeliveryOutcomeV1],
]
