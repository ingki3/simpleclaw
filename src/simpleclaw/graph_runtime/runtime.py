"""Delivery journal과 ConversationStore persistence recovery facade."""

from __future__ import annotations

import hashlib
import inspect
import sqlite3
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .adapters.delivery import DeliveryAdapter
from .contracts import DeliveryIntentV1
from .events import DeliveryReceiptV1
from .idempotency import (
    IdempotencyInvariantError,
    UniquePayloadLedger,
    persistence_id,
)
from .status import DeliveryStatus


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DeliveryJournalEntry:
    intent: DeliveryIntentV1
    content_hash: str
    dispatching_attempt: int | None = None
    receipt: DeliveryReceiptV1 | None = None


class DeliveryJournal(Protocol):
    def get(self, delivery_id: str) -> DeliveryJournalEntry | None: ...
    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None: ...
    def mark_dispatching(self, delivery_id: str, *, attempt: int) -> None: ...
    def clear_failed_before_send(self, delivery_id: str) -> None: ...
    def record_receipt(self, receipt: DeliveryReceiptV1) -> None: ...


class InMemoryDeliveryJournal:
    """테스트/주입용 journal. production은 같은 API의 durable 구현을 주입한다."""

    def __init__(self) -> None:
        self._entries: dict[str, DeliveryJournalEntry] = {}

    def get(self, delivery_id: str) -> DeliveryJournalEntry | None:
        return self._entries.get(delivery_id)

    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None:
        content_hash = _content_hash(content)
        entry = self._entries.get(intent.delivery_id)
        if entry is None:
            self._entries[intent.delivery_id] = DeliveryJournalEntry(
                intent=intent, content_hash=content_hash
            )
            return
        if entry.intent != intent or entry.content_hash != content_hash:
            raise IdempotencyInvariantError(
                "delivery_id already exists with a different intent or payload"
            )

    def mark_dispatching(self, delivery_id: str, *, attempt: int) -> None:
        entry = self._entries[delivery_id]
        self._entries[delivery_id] = DeliveryJournalEntry(
            intent=entry.intent,
            content_hash=entry.content_hash,
            dispatching_attempt=attempt,
            receipt=entry.receipt,
        )

    def clear_failed_before_send(self, delivery_id: str) -> None:
        entry = self._entries[delivery_id]
        self._entries[delivery_id] = DeliveryJournalEntry(
            intent=entry.intent, content_hash=entry.content_hash
        )

    def record_receipt(self, receipt: DeliveryReceiptV1) -> None:
        entry = self._entries[receipt.delivery_id]
        if entry.receipt is not None and entry.receipt != receipt:
            raise IdempotencyInvariantError("delivery receipt is write-once")
        self._entries[receipt.delivery_id] = DeliveryJournalEntry(
            intent=entry.intent,
            content_hash=entry.content_hash,
            receipt=receipt,
        )


class SQLiteDeliveryJournal:
    """process crash 뒤에도 sender-started 상태와 receipt를 보존하는 outbox다."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_delivery_journal (
                    delivery_id TEXT PRIMARY KEY,
                    intent_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    dispatching_attempt INTEGER,
                    receipt_json TEXT
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def get(self, delivery_id: str) -> DeliveryJournalEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT intent_json, content_hash, dispatching_attempt, receipt_json "
                "FROM graph_delivery_journal WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryJournalEntry(
            intent=DeliveryIntentV1.model_validate_json(row[0]),
            content_hash=str(row[1]),
            dispatching_attempt=(int(row[2]) if row[2] is not None else None),
            receipt=(
                DeliveryReceiptV1.model_validate_json(row[3])
                if row[3] is not None
                else None
            ),
        )

    def record_intent(self, intent: DeliveryIntentV1, content: str) -> None:
        content_hash = _content_hash(content)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT intent_json, content_hash FROM graph_delivery_journal "
                "WHERE delivery_id = ?",
                (intent.delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO graph_delivery_journal "
                    "(delivery_id, intent_json, content_hash) VALUES (?, ?, ?)",
                    (intent.delivery_id, intent.model_dump_json(), content_hash),
                )
                return
        if (
            DeliveryIntentV1.model_validate_json(row[0]) != intent
            or str(row[1]) != content_hash
        ):
            raise IdempotencyInvariantError(
                "delivery_id already exists with a different intent or payload"
            )

    def mark_dispatching(self, delivery_id: str, *, attempt: int) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = ? "
                "WHERE delivery_id = ? AND receipt_json IS NULL",
                (attempt, delivery_id),
            )
            if cursor.rowcount != 1:
                raise IdempotencyInvariantError(
                    "cannot mark missing or completed delivery as dispatching"
                )

    def clear_failed_before_send(self, delivery_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL "
                "WHERE delivery_id = ? AND receipt_json IS NULL",
                (delivery_id,),
            )

    def record_receipt(self, receipt: DeliveryReceiptV1) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT receipt_json FROM graph_delivery_journal "
                "WHERE delivery_id = ?",
                (receipt.delivery_id,),
            ).fetchone()
            if row is None:
                raise IdempotencyInvariantError("delivery intent is missing")
            if row[0] is not None:
                if DeliveryReceiptV1.model_validate_json(row[0]) != receipt:
                    raise IdempotencyInvariantError("delivery receipt is write-once")
                return
            conn.execute(
                "UPDATE graph_delivery_journal SET dispatching_attempt = NULL, "
                "receipt_json = ? WHERE delivery_id = ?",
                (receipt.model_dump_json(), receipt.delivery_id),
            )


class DeliveryRuntime:
    def __init__(
        self,
        *,
        journal: DeliveryJournal,
        adapters: Mapping[str, DeliveryAdapter],
    ) -> None:
        self._journal = journal
        self._adapters = dict(adapters)

    async def deliver(
        self, intent: DeliveryIntentV1, content: str
    ) -> DeliveryReceiptV1:
        self._journal.record_intent(intent, content)
        entry = self._journal.get(intent.delivery_id)
        assert entry is not None
        if entry.receipt is not None:
            return entry.receipt
        if entry.dispatching_attempt is not None:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.UNKNOWN,
                attempt=entry.dispatching_attempt,
                detail="sender started but no durable receipt was found",
            )
            self._journal.record_receipt(receipt)
            return receipt

        if intent.status is DeliveryStatus.SHADOWED:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.SHADOWED,
                attempt=1,
            )
            self._journal.record_receipt(receipt)
            return receipt

        adapter = self._adapters.get(intent.channel)
        if adapter is None:
            receipt = self._receipt(
                intent,
                status=DeliveryStatus.FAILED_BEFORE_SEND,
                attempt=1,
                detail=f"no delivery adapter for channel {intent.channel!r}",
            )
            self._journal.record_receipt(receipt)
            return receipt

        last: DeliveryReceiptV1 | None = None
        for attempt in range(1, intent.max_attempts + 1):
            self._journal.mark_dispatching(intent.delivery_id, attempt=attempt)
            result = await adapter.send(intent, content)
            last = self._receipt(
                intent,
                status=result.status,
                attempt=attempt,
                external_message_id=result.external_message_id,
                detail=result.detail,
            )
            if (
                result.status is DeliveryStatus.FAILED_BEFORE_SEND
                and attempt < intent.max_attempts
            ):
                self._journal.clear_failed_before_send(intent.delivery_id)
                continue
            self._journal.record_receipt(last)
            return last
        assert last is not None
        self._journal.record_receipt(last)
        return last

    @staticmethod
    def _receipt(
        intent: DeliveryIntentV1,
        *,
        status: DeliveryStatus,
        attempt: int,
        external_message_id: str | None = None,
        detail: str | None = None,
    ) -> DeliveryReceiptV1:
        return DeliveryReceiptV1(
            receipt_id=f"{intent.delivery_id}:{attempt}:{status.value}",
            request_id=intent.request_id,
            delivery_id=intent.delivery_id,
            sequence=attempt,
            occurred_at=datetime.now(UTC),
            status=status,
            attempt=attempt,
            external_message_id=external_message_id,
            detail=detail,
        )


@dataclass(frozen=True, slots=True)
class PersistenceReceiptV1:
    persistence_id: str
    payload_hash: str
    persisted: bool


class InMemoryPersistenceJournal:
    def __init__(self) -> None:
        self.intents = UniquePayloadLedger()
        self.receipts: dict[str, PersistenceReceiptV1] = {}


PersistenceWriter = Callable[[str, str, str], None | Awaitable[None]]


class PersistenceRuntime:
    """DELIVERED 결과만 writer에 넘기며 recovery 시 delivery를 호출하지 않는다."""

    def __init__(
        self,
        *,
        journal: InMemoryPersistenceJournal,
        writer: PersistenceWriter,
    ) -> None:
        self._journal = journal
        self._writer = writer

    async def persist_delivered(
        self,
        *,
        session_key: str,
        request_id: str,
        artifact_hash: str,
        content: str,
        delivery_receipt: DeliveryReceiptV1,
    ) -> PersistenceReceiptV1 | None:
        if delivery_receipt.status is not DeliveryStatus.DELIVERED:
            return None
        identity = persistence_id(session_key, request_id, artifact_hash)
        payload_hash = _content_hash(content)
        self._journal.intents.record(identity, payload_hash)
        existing = self._journal.receipts.get(identity)
        if existing is not None:
            return existing
        result = self._writer(identity, payload_hash, content)
        if inspect.isawaitable(result):
            await result
        receipt = PersistenceReceiptV1(identity, payload_hash, True)
        self._journal.receipts[identity] = receipt
        return receipt

    @staticmethod
    def delivery_receipt_for_test(
        *, delivery_id: str, status: DeliveryStatus
    ) -> DeliveryReceiptV1:
        return DeliveryReceiptV1(
            receipt_id=f"test:{delivery_id}:{status.value}",
            request_id="request-1",
            delivery_id=delivery_id,
            sequence=1,
            occurred_at=datetime.now(UTC),
            status=status,
        )
