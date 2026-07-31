"""Persistent session identity and pending-interaction state.

``SessionState`` deliberately owns only state that survives across turns.  It
must never become a cache for semantic decisions such as domain, intent,
route, or evidence; those belong to :mod:`simpleclaw.agent.turn_state`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_PENDING_PAYLOAD_VERSION = 1

current_session_key_var: ContextVar[str] = ContextVar(
    "simpleclaw_current_session_key",
    default="legacy-default",
)
current_turn_id_var: ContextVar[str | None] = ContextVar(
    "simpleclaw_current_turn_id",
    default=None,
)


@dataclass(frozen=True)
class SessionIdentity:
    """Stable channel/user/conversation boundary without exposing raw IDs."""

    channel: str
    user_id: str
    chat_id: str
    thread_id: str = ""

    def stable_key(self) -> str:
        canonical = json.dumps(
            [
                str(self.channel),
                str(self.user_id),
                str(self.chat_id),
                str(self.thread_id),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PendingInteraction:
    """Versioned serializable clarification/confirmation payload."""

    kind: str
    payload: Mapping[str, Any]
    version: int = _PENDING_PAYLOAD_VERSION

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "kind": self.kind,
                "payload": dict(self.payload),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> PendingInteraction | None:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Ignoring malformed pending interaction payload")
            return None
        if not isinstance(decoded, dict):
            logger.warning("Ignoring non-object pending interaction payload")
            return None
        if decoded.get("version") != _PENDING_PAYLOAD_VERSION:
            logger.warning(
                "Ignoring unsupported pending interaction version: %r",
                decoded.get("version"),
            )
            return None
        kind = decoded.get("kind")
        payload = decoded.get("payload")
        if not isinstance(kind, str) or not kind or not isinstance(payload, dict):
            logger.warning("Ignoring invalid pending interaction shape")
            return None
        return cls(kind=kind, payload=payload)


@dataclass(frozen=True)
class SessionState:
    """Durable state owned by one stable session key."""

    key: str
    pending: PendingInteraction | None = None
    last_completed_turn_id: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now().astimezone())

    def to_record(self) -> dict[str, str | None]:
        return {
            "session_key": self.key,
            "pending_kind": self.pending.kind if self.pending else None,
            "pending_payload": self.pending.to_json() if self.pending else None,
            "last_completed_turn_id": self.last_completed_turn_id,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> SessionState:
        pending_raw = record.get("pending_payload")
        pending = (
            PendingInteraction.from_json(str(pending_raw))
            if pending_raw
            else None
        )
        updated_raw = record.get("updated_at")
        try:
            updated_at = datetime.fromisoformat(str(updated_raw))
        except (TypeError, ValueError):
            updated_at = datetime.now().astimezone()
        return cls(
            key=str(record.get("session_key") or ""),
            pending=pending,
            last_completed_turn_id=(
                str(record["last_completed_turn_id"])
                if record.get("last_completed_turn_id")
                else None
            ),
            updated_at=updated_at,
        )
