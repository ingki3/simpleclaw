"""Bounded, ID-bearing conversation context for the Unified TurnPlanner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Sequence

from simpleclaw.memory.models import ConversationMessage, MessageRole

_DEFAULT_MAX_CHARS_PER_TURN = 2400


class ContextTrust(str, Enum):
    """How a planner may treat a context candidate."""

    USER_INPUT = "user_input"
    ASSISTANT_CONTEXT_ONLY = "assistant_context_only"
    SYSTEM_CONTEXT_ONLY = "system_context_only"


@dataclass(frozen=True)
class ContextCandidate:
    """A stable conversation row prepared for planner context selection."""

    turn_id: str
    role: str
    timestamp: datetime
    content: str
    trust: ContextTrust
    evidence_eligible: bool = False

    def to_prompt_payload(self) -> dict[str, Any]:
        """Return the compact, deterministic shape sent to a planner."""
        return {
            "id": self.turn_id,
            "role": self.role,
            "timestamp": self.timestamp.isoformat(),
            "content": self.content,
            "trust": self.trust.value,
            "evidence_eligible": self.evidence_eligible,
        }


@dataclass(frozen=True)
class ContextCandidateSet:
    """A chronological collection of context candidates and budget metadata."""

    candidates: tuple[ContextCandidate, ...]
    total_chars: int
    truncated: bool

    def to_prompt_json(self) -> str:
        """Serialize candidates in their stable chronological order."""
        return json.dumps(
            [candidate.to_prompt_payload() for candidate in self.candidates],
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ContextCandidateBuilder:
    """Build recent planner candidates under deterministic turn/char budgets."""

    def __init__(
        self,
        *,
        max_turns: int = 8,
        max_chars: int = 6000,
        max_chars_per_turn: int = _DEFAULT_MAX_CHARS_PER_TURN,
    ) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be greater than zero")
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than zero")
        if max_chars_per_turn <= 0:
            raise ValueError("max_chars_per_turn must be greater than zero")
        self._max_turns = max_turns
        self._max_chars = max_chars
        self._max_chars_per_turn = max_chars_per_turn

    def build(
        self,
        rows: Sequence[tuple[int, ConversationMessage]],
    ) -> ContextCandidateSet:
        """Return newest bounded rows while preserving oldest-to-newest order."""
        window = rows[-self._max_turns :]
        selected: list[ContextCandidate] = []
        used = 0
        truncated = len(rows) > len(window)

        for row_id, message in reversed(window):
            content = " ".join(message.content.split())
            if not content:
                truncated = True
                continue

            remaining = self._max_chars - used
            if remaining <= 0:
                truncated = True
                break

            allowed = min(remaining, self._max_chars_per_turn)
            if len(content) > allowed:
                content = content[:allowed]
                truncated = True

            selected.append(
                ContextCandidate(
                    turn_id=f"msg:{row_id}",
                    role=message.role.value,
                    timestamp=message.timestamp,
                    content=content,
                    trust=_trust_for_role(message.role),
                    evidence_eligible=False,
                )
            )
            used += len(content)

        selected.reverse()
        return ContextCandidateSet(tuple(selected), used, truncated)


def _trust_for_role(role: MessageRole) -> ContextTrust:
    if role is MessageRole.USER:
        return ContextTrust.USER_INPUT
    if role is MessageRole.ASSISTANT:
        return ContextTrust.ASSISTANT_CONTEXT_ONLY
    return ContextTrust.SYSTEM_CONTEXT_ONLY
