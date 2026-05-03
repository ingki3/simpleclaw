"""Dreaming suggestion queue + reject blocklist (BIZ-79).

Dreaming-derived insights are queued here as **pending suggestions** by
default. Operators review via the Admin UI Memory screen with three actions:

- **accept**: text is appended to USER.md as-is.
- **edit**: operator-supplied text is appended to USER.md instead.
- **reject**: the topic is added to the blocklist; future dreaming cycles
  skip the topic so the same insight cannot be re-extracted.

Auto-promotion bypasses the queue: insights that already meet **both** the
configured ``confidence`` and ``evidence_count`` thresholds skip the queue
and apply directly to USER.md (no review required). The thresholds are
both required — a high-confidence one-shot is still queued, and a
low-confidence repeat is still queued. This dual gate is the BIZ-79 DoD.

Persistence:
- ``.agent/suggestions.jsonl`` — one ``SuggestionMeta`` row per line.
- ``.agent/insight_blocklist.jsonl`` — one normalized topic key per line.
- Both use atomic tmp+rename writes (same pattern as ``InsightStore``).

Status lifecycle: ``pending → accepted | edited | rejected`` (terminal).

BIZ-78 (decay/blocklist) — long-term plan replaces this blocklist with a
richer model (decay window, per-source weights). For BIZ-79 we just need a
hard "skip this topic" signal so the reject loop is verifiable end-to-end.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.insights import InsightMeta, normalize_topic

logger = logging.getLogger(__name__)


# Status string literals — kept as plain strings (not Enum) so JSONL stays
# round-trip-safe with hand-edits and external tools (jq, grep).
SuggestionStatus = str
VALID_STATUSES: tuple[str, ...] = ("pending", "accepted", "edited", "rejected")
TERMINAL_STATUSES: tuple[str, ...] = ("accepted", "edited", "rejected")


@dataclass
class SuggestionMeta:
    """A single dreaming-derived suggestion awaiting operator review.

    Carries enough context for the Admin UI to render the row (topic, text,
    confidence, evidence_count) **and** to link back to source messages
    (start/end_msg_id, source_msg_ids — the BIZ-77 linkage).
    """

    id: str
    topic: str
    text: str
    confidence: float = 0.0
    evidence_count: int = 1
    source_msg_ids: list[int] = field(default_factory=list)
    start_msg_id: int | None = None
    end_msg_id: int | None = None
    status: SuggestionStatus = "pending"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    # Populated only when status == "edited" (operator-supplied replacement).
    edited_text: str | None = None
    # Populated only when status == "rejected" (free-form operator reason).
    reject_reason: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> SuggestionMeta:
        created = d.get("created_at")
        updated = d.get("updated_at")
        raw_start = d.get("start_msg_id")
        raw_end = d.get("end_msg_id")
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            topic=str(d.get("topic", "")),
            text=str(d.get("text", "")),
            confidence=float(d.get("confidence", 0.0)),
            evidence_count=int(d.get("evidence_count", 1)),
            source_msg_ids=list(d.get("source_msg_ids") or []),
            start_msg_id=(int(raw_start) if raw_start is not None else None),
            end_msg_id=(int(raw_end) if raw_end is not None else None),
            status=str(d.get("status") or "pending"),
            created_at=(
                datetime.fromisoformat(created)
                if isinstance(created, str)
                else datetime.now()
            ),
            updated_at=(
                datetime.fromisoformat(updated)
                if isinstance(updated, str)
                else datetime.now()
            ),
            edited_text=d.get("edited_text"),
            reject_reason=d.get("reject_reason"),
        )

    @classmethod
    def from_insight(cls, meta: InsightMeta) -> SuggestionMeta:
        """Build a fresh pending suggestion from an ``InsightMeta`` observation."""
        return cls(
            id=uuid.uuid4().hex,
            topic=meta.topic,
            text=meta.text,
            confidence=meta.confidence,
            evidence_count=meta.evidence_count,
            source_msg_ids=list(meta.source_msg_ids),
            start_msg_id=meta.start_msg_id,
            end_msg_id=meta.end_msg_id,
            status="pending",
        )

    @property
    def applied_text(self) -> str:
        """Text to write to USER.md when accepted — edit overrides original."""
        if self.edited_text is not None and self.edited_text.strip():
            return self.edited_text
        return self.text


class SuggestionStore:
    """JSONL suggestion sidecar.

    Reads load all rows; writes serialize all rows back via tmp+rename. Designed
    for the dreaming pipeline + admin API to share within a single-process
    daemon (no concurrent writers — locking is unnecessary).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[SuggestionMeta]:
        out: list[SuggestionMeta] = []
        if not self._path.is_file():
            return out
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to read suggestions sidecar %s: %s", self._path, exc
            )
            return out
        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(SuggestionMeta.from_dict(d))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping malformed suggestion line %d in %s: %s",
                    line_no,
                    self._path,
                    exc,
                )
                continue
        return out

    def save_all(self, items: list[SuggestionMeta]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for s in items:
                f.write(json.dumps(s.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def get(self, suggestion_id: str) -> SuggestionMeta | None:
        for s in self.load():
            if s.id == suggestion_id:
                return s
        return None

    def list_pending(self) -> list[SuggestionMeta]:
        # Most-recent first — Admin UI should show newest dreaming output on top.
        items = [s for s in self.load() if s.status == "pending"]
        items.sort(key=lambda s: s.updated_at, reverse=True)
        return items

    def list_all(self) -> list[SuggestionMeta]:
        return self.load()

    def find_pending_by_topic(self, topic: str) -> SuggestionMeta | None:
        """Return the existing pending row for this topic (normalized), if any."""
        key = normalize_topic(topic)
        if not key:
            return None
        for s in self.load():
            if s.status == "pending" and normalize_topic(s.topic) == key:
                return s
        return None

    def upsert_pending(self, observation: InsightMeta) -> SuggestionMeta:
        """Insert a new pending suggestion, or refresh an existing pending row.

        Idempotency: at most one *pending* row per normalized topic. If a
        suggestion for the same topic is already pending we update its
        evidence_count / confidence / source_msg_ids in place — operators see
        a single row that strengthens over cycles instead of a growing pile of
        near-duplicates. Terminal rows (accepted / edited / rejected) for the
        same topic are left alone (audit trail).
        """
        items = self.load()
        key = normalize_topic(observation.topic)
        now = datetime.now()
        for i, s in enumerate(items):
            if s.status == "pending" and normalize_topic(s.topic) == key:
                s.text = observation.text or s.text
                s.confidence = observation.confidence
                s.evidence_count = observation.evidence_count
                s.source_msg_ids = list(observation.source_msg_ids)
                s.start_msg_id = observation.start_msg_id
                s.end_msg_id = observation.end_msg_id
                s.updated_at = now
                items[i] = s
                self.save_all(items)
                return s

        new = SuggestionMeta.from_insight(observation)
        new.created_at = now
        new.updated_at = now
        items.append(new)
        self.save_all(items)
        return new

    def update_status(
        self,
        suggestion_id: str,
        status: SuggestionStatus,
        *,
        edited_text: str | None = None,
        reject_reason: str | None = None,
    ) -> SuggestionMeta | None:
        """Mutate a row's status. Returns the updated row, or None if not found.

        Terminal status transitions are accepted (accept→accept is a no-op
        update of timestamps; accept→reject is allowed for "I changed my mind"
        flows). The Admin API enforces stricter business rules on top of this
        primitive (e.g. only pending rows are visible to accept/edit/reject).
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid suggestion status: {status}")
        items = self.load()
        target: SuggestionMeta | None = None
        for s in items:
            if s.id == suggestion_id:
                target = s
                break
        if target is None:
            return None
        target.status = status
        target.updated_at = datetime.now()
        if edited_text is not None:
            target.edited_text = edited_text
        if reject_reason is not None:
            target.reject_reason = reject_reason
        self.save_all(items)
        return target


class BlocklistStore:
    """Topic-level blocklist for rejected insights.

    Stores normalized topic keys so spelling/spacing variants (e.g.
    "맥북에어 가격" vs "맥북에어가격") block the same underlying topic. JSONL
    sidecar at ``.agent/insight_blocklist.jsonl``.

    BIZ-78 (Blocklist + Decay) will replace this with a richer model. For
    BIZ-79 we just need the hard signal "skip this topic in future dreaming"
    so the reject → no re-extraction loop is verifiable.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if not self._path.is_file():
            return out
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read blocklist %s: %s", self._path, exc)
            return out
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = normalize_topic(str(d.get("topic", "")))
            if not key:
                continue
            out[key] = d
        return out

    def is_blocked(self, topic: str) -> bool:
        key = normalize_topic(topic)
        if not key:
            return False
        return key in self.load()

    def add(self, topic: str, *, reason: str | None = None) -> None:
        items = self.load()
        key = normalize_topic(topic)
        if not key:
            return
        items[key] = {
            "topic": topic.strip(),
            "topic_key": key,
            "reason": reason or "",
            "blocked_at": datetime.now().isoformat(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for entry in items.values():
                f.write(json.dumps(entry, ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def list_all(self) -> list[dict]:
        return list(self.load().values())


__all__ = [
    "SuggestionMeta",
    "SuggestionStore",
    "BlocklistStore",
    "VALID_STATUSES",
    "TERMINAL_STATUSES",
]
