"""Proactive opportunity queue의 공통 데이터 모델.

detector/presenter/action executor가 직접 결합되지 않도록, 후보 제안과 정책 판정에
필요한 최소 필드를 dataclass와 Enum으로 고정한다. JSONL 저장소가 외부 도구로도
읽히도록 모든 모델은 dict round-trip을 명시적으로 제공한다.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class OpportunityType(StrEnum):
    """후속 detector들이 생성할 proactive 후보의 의미적 종류."""

    REPEATED_REQUEST = "repeated_request"
    REQUESTED_FOLLOWUP = "requested_followup"
    UNFINISHED_FOLLOWUP = "unfinished_followup"
    INTEREST_BRIEFING = "interest_briefing"
    CONTEXTUAL_REMINDER = "contextual_reminder"
    FAILURE_RECOVERY = "failure_recovery"
    OTHER = "other"


class OpportunityStatus(StrEnum):
    """Opportunity Queue에 남는 후보의 상태 전이 값."""

    PENDING = "pending"
    SENT = "sent"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


class SuggestedActionKind(StrEnum):
    """사용자에게 제안할 수 있는 후속 액션 종류."""

    NONE = "none"
    CREATE_CRON = "create_cron"
    RUN_RECIPE = "run_recipe"
    OPEN_REVIEW = "open_review"
    SEND_MESSAGE = "send_message"
    CUSTOM = "custom"


class PolicyDecisionAction(StrEnum):
    """TPO 정책 엔진이 presenter에 반환하는 판정 액션."""

    SEND_NOW = "send_now"
    DEFER = "defer"
    SUPPRESS = "suppress"
    NEEDS_USER_APPROVAL = "needs_user_approval"


TERMINAL_STATUSES: tuple[OpportunityStatus, ...] = (
    OpportunityStatus.ACCEPTED,
    OpportunityStatus.DISMISSED,
    OpportunityStatus.EXPIRED,
    OpportunityStatus.EXECUTED,
    OpportunityStatus.FAILED,
)


def _dt_to_str(value: datetime | None) -> str | None:
    """JSON 직렬화를 위해 datetime을 ISO 문자열로 바꾼다."""
    return value.isoformat() if value is not None else None


def _dt_from_value(value: object) -> datetime | None:
    """저장된 ISO 문자열을 datetime으로 복원하되 잘못된 값은 None으로 둔다."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


@dataclass
class SuggestedAction:
    """후보가 승인/수락될 때 실행하거나 안내할 액션 설명."""

    kind: SuggestedActionKind = SuggestedActionKind.NONE
    label: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSONL 저장용 dict로 변환한다."""
        return {"kind": self.kind.value, "label": self.label, "payload": dict(self.payload)}

    @classmethod
    def from_dict(cls, data: object) -> SuggestedAction:
        """dict/None 입력에서 SuggestedAction을 복원한다."""
        if not isinstance(data, dict):
            return cls()
        try:
            kind = SuggestedActionKind(str(data.get("kind") or SuggestedActionKind.NONE))
        except ValueError:
            kind = SuggestedActionKind.CUSTOM
        payload = data.get("payload")
        return cls(
            kind=kind,
            label=str(data.get("label") or ""),
            payload=dict(payload) if isinstance(payload, dict) else {},
        )


@dataclass
class ProactiveOpportunity:
    """detector가 큐에 적재하고 presenter가 사용자에게 노출할 후보."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: OpportunityType = OpportunityType.OTHER
    title: str = ""
    message_draft: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    priority: int = 0
    urgency: int = 0
    cooldown_key: str = ""
    suggested_action: SuggestedAction = field(default_factory=SuggestedAction)
    requires_user_approval: bool = False
    status: OpportunityStatus = OpportunityStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    last_presented_at: datetime | None = None
    presented_count: int = 0
    source: str = ""
    source_msg_ids: list[int] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)
    error_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSONL 저장과 API 응답에 사용할 dict로 변환한다."""
        data = asdict(self)
        data["type"] = self.type.value
        data["status"] = self.status.value
        data["suggested_action"] = self.suggested_action.to_dict()
        data["created_at"] = _dt_to_str(self.created_at)
        data["expires_at"] = _dt_to_str(self.expires_at)
        data["last_presented_at"] = _dt_to_str(self.last_presented_at)
        data["updated_at"] = _dt_to_str(self.updated_at)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProactiveOpportunity:
        """저장된 dict를 ProactiveOpportunity로 복원한다."""
        try:
            opportunity_type = OpportunityType(str(data.get("type") or OpportunityType.OTHER))
        except ValueError:
            opportunity_type = OpportunityType.OTHER
        try:
            status = OpportunityStatus(str(data.get("status") or OpportunityStatus.PENDING))
        except ValueError:
            status = OpportunityStatus.PENDING
        created_at = _dt_from_value(data.get("created_at")) or datetime.now()
        updated_at = _dt_from_value(data.get("updated_at")) or created_at
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            type=opportunity_type,
            title=str(data.get("title") or ""),
            message_draft=str(data.get("message_draft") or ""),
            evidence=list(data.get("evidence") or []),
            confidence=float(data.get("confidence") or 0.0),
            priority=int(data.get("priority") or 0),
            urgency=int(data.get("urgency") or 0),
            cooldown_key=str(data.get("cooldown_key") or ""),
            suggested_action=SuggestedAction.from_dict(data.get("suggested_action")),
            requires_user_approval=bool(data.get("requires_user_approval", False)),
            status=status,
            created_at=created_at,
            expires_at=_dt_from_value(data.get("expires_at")),
            last_presented_at=_dt_from_value(data.get("last_presented_at")),
            presented_count=int(data.get("presented_count") or 0),
            source=str(data.get("source") or ""),
            source_msg_ids=[int(v) for v in list(data.get("source_msg_ids") or [])],
            updated_at=updated_at,
            error_summary=str(data.get("error_summary") or ""),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        """현재 시각 기준 만료 여부를 판단한다."""
        if self.expires_at is None:
            return False
        return self.expires_at <= (now or datetime.now())


@dataclass
class TPOContext:
    """정책 평가 시점의 TPO/운영 제한 값 묶음."""

    now: datetime = field(default_factory=datetime.now)
    enabled: bool = False
    mode: str = "low"
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "08:00"
    max_messages_per_day: int = 1
    sent_today_count: int = 0
    topic_cooldown_days: int = 14
    dismissed_cooldown_days: int = 30
    min_confidence: float = 0.75
    last_sent_at: datetime | None = None
    last_dismissed_at: datetime | None = None


@dataclass
class PolicyDecision:
    """TPO 정책 엔진의 구조화된 판정 결과."""

    action: PolicyDecisionAction
    reasons: list[str] = field(default_factory=list)
    defer_until: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """로그/API에 쓰기 쉬운 dict로 변환한다."""
        return {
            "action": self.action.value,
            "reasons": list(self.reasons),
            "defer_until": _dt_to_str(self.defer_until),
        }

    @property
    def should_send(self) -> bool:
        """presenter가 즉시 발송할 수 있는지 빠르게 확인한다."""
        return self.action == PolicyDecisionAction.SEND_NOW
