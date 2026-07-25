"""Unified TurnPlanner에 전달할 ID 기반 대화 문맥 후보를 예산 안에서 구성한다."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from simpleclaw.memory.models import ConversationMessage, MessageRole

_DEFAULT_MAX_CHARS_PER_TURN = 2400


class ContextTrust(str, Enum):
    """Planner가 문맥 후보를 신뢰하고 사용할 수 있는 범위를 구분한다."""

    USER_INPUT = "user_input"
    ASSISTANT_CONTEXT_ONLY = "assistant_context_only"
    SYSTEM_CONTEXT_ONLY = "system_context_only"


@dataclass(frozen=True)
class ContextCandidate:
    """Planner의 문맥 선택에 사용할 안정적인 대화 행을 표현한다."""

    turn_id: str
    role: str
    timestamp: datetime
    content: str
    trust: ContextTrust
    evidence_eligible: bool = False

    def to_prompt_payload(self) -> dict[str, Any]:
        """Planner 입력의 필드 순서를 고정하기 위해 간결한 payload를 반환한다."""
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
    """시간순 문맥 후보와 예산 적용 결과를 함께 보관한다."""

    candidates: tuple[ContextCandidate, ...]
    total_chars: int
    truncated: bool

    def to_prompt_json(self) -> str:
        """후보 순서를 안정적으로 유지해 JSON으로 직렬화한다."""
        return json.dumps(
            [candidate.to_prompt_payload() for candidate in self.candidates],
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ContextCandidateBuilder:
    """결정적인 turn·문자 예산 안에서 최신 Planner 후보를 구성한다."""

    def __init__(
        self,
        *,
        max_turns: int = 8,
        max_chars: int = 6000,
        max_chars_per_turn: int = _DEFAULT_MAX_CHARS_PER_TURN,
    ) -> None:
        """후보 집합마다 동일한 예산 정책을 적용하도록 한도를 검증해 저장한다."""
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
        """최신 행부터 예산을 배분한 뒤 오래된 순서로 복원해 반환한다."""
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
    """메시지 역할별 허용 범위를 고정해 과거 응답의 근거 승격을 막는다."""
    if role is MessageRole.USER:
        return ContextTrust.USER_INPUT
    if role is MessageRole.ASSISTANT:
        return ContextTrust.ASSISTANT_CONTEXT_ONLY
    return ContextTrust.SYSTEM_CONTEXT_ONLY
