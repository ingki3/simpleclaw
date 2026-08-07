"""V4 primary final을 실제 channel delivery와 durable persistence에 연결한다."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
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
from simpleclaw.graph_runtime.idempotency import (
    delivery_id,
    persistence_id,
    validate_canonical_artifact_identity,
)
from simpleclaw.graph_runtime.runtime import (
    DeliveryRuntime,
    InMemoryPersistenceJournal,
    PersistenceReceiptV1,
    PersistenceRuntime,
    SQLiteDeliveryJournal,
)
from simpleclaw.graph_runtime.status import DeliveryStatus

logger = logging.getLogger(__name__)


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
    persistence_status: PrimaryPersistenceStatus
    persistence_error_type: str | None = None

    @property
    def complete_success(self) -> bool:
        """Telegram delivery와 assistant persistence가 모두 끝났는지 반환한다."""
        return (
            self.delivery_receipt.status is DeliveryStatus.DELIVERED
            and self.persistence_status is PrimaryPersistenceStatus.PERSISTED
        )


class PrimaryPersistenceStatus(str, Enum):
    """V4 primary user-visible delivery 이후의 persistence 상태다."""

    NOT_ATTEMPTED = "not_attempted"
    PERSISTED = "persisted"
    FAILED = "failed"


class PrimaryDeliveryCoordinator:
    """Actual Telegram send와 delivered assistant write를 한 identity로 직렬화한다."""

    def __init__(
        self,
        *,
        journal_path: str | Path,
        conversation_store,
        delivery_lease_seconds: float = 30.0,
        delivery_poll_interval: float = 0.01,
        persistence_max_attempts: int = 3,
        persistence_retry_interval: float = 0.05,
    ) -> None:
        if persistence_max_attempts < 1:
            raise ValueError("persistence_max_attempts must be at least 1")
        if persistence_retry_interval < 0:
            raise ValueError("persistence_retry_interval must be non-negative")
        self._delivery_journal = SQLiteDeliveryJournal(journal_path)
        self._store = conversation_store
        self._delivery_lease_seconds = delivery_lease_seconds
        self._delivery_poll_interval = delivery_poll_interval
        self._persistence_max_attempts = persistence_max_attempts
        self._persistence_retry_interval = persistence_retry_interval

    async def deliver_telegram(
        self,
        response: PrimaryResponseText,
        *,
        destination_ref: str,
        sender: SenderCallback,
    ) -> PrimaryDeliveryOutcomeV1:
        metadata = response.metadata
        validate_canonical_artifact_identity(
            request_id=metadata.request_id,
            content=str(response),
            artifact_id=metadata.artifact_id,
            content_hash=metadata.artifact_hash,
        )
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
            return PrimaryDeliveryOutcomeV1(
                receipt,
                None,
                PrimaryPersistenceStatus.NOT_ATTEMPTED,
            )

        persistence_identity = persistence_id(
            metadata.session_key,
            metadata.request_id,
            metadata.artifact_hash,
        )
        payload_hash = hashlib.sha256(str(response).encode()).hexdigest()
        for attempt in range(1, self._persistence_max_attempts + 1):
            try:
                if self._store.get_outbound_persistence(
                    persistence_identity,
                    payload_hash=payload_hash,
                ) is not None:
                    self._store.bind_outbound_to_turn(
                        persistence_identity,
                        payload_hash=payload_hash,
                        turn_id=metadata.request_id,
                    )
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
                            request_id=metadata.request_id,
                        ),
                    ).persist_delivered(
                        session_key=metadata.session_key,
                        request_id=metadata.request_id,
                        artifact_hash=metadata.artifact_hash,
                        content=str(response),
                        delivery_receipt=receipt,
                    )
                break
            except Exception as exc:
                if attempt >= self._persistence_max_attempts:
                    logger.exception(
                        "V4 primary assistant persistence exhausted after Telegram "
                        "delivery: request_id=%s delivery_id=%s attempts=%d "
                        "error_type=%s",
                        metadata.request_id,
                        receipt.delivery_id,
                        attempt,
                        type(exc).__name__,
                    )
                    return PrimaryDeliveryOutcomeV1(
                        receipt,
                        None,
                        PrimaryPersistenceStatus.FAILED,
                        type(exc).__name__,
                    )
                logger.warning(
                    "V4 primary assistant persistence retrying after Telegram "
                    "delivery: request_id=%s delivery_id=%s attempt=%d/%d "
                    "error_type=%s",
                    metadata.request_id,
                    receipt.delivery_id,
                    attempt,
                    self._persistence_max_attempts,
                    type(exc).__name__,
                )
                await asyncio.sleep(
                    self._persistence_retry_interval * (2 ** (attempt - 1))
                )
        return PrimaryDeliveryOutcomeV1(
            receipt,
            persisted,
            PrimaryPersistenceStatus.PERSISTED,
        )


PrimaryDeliveryHandler = Callable[
    [PrimaryResponseText, str, SenderCallback],
    Awaitable[PrimaryDeliveryOutcomeV1],
]
