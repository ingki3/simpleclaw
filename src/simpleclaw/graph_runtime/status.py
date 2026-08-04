"""V4 graph 런타임의 공통 lifecycle과 outcome 어휘를 정의한다.

이 enum들은 흐름과 side effect 상태만 표현한다. 자산이 소유한 payload 의미는
자산 계약에만 속하므로 이 모듈에 추가하지 않는다.
"""

from __future__ import annotations

from enum import Enum


class LifecycleStatus(str, Enum):
    NEW = "new"
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    TERMINAL = "terminal"


class PlanStatus(str, Enum):
    ABSENT = "absent"
    PROPOSED = "proposed"
    REPAIRABLE = "repairable"
    VALID = "valid"
    NEEDS_INPUT = "needs_input"
    INVALID = "invalid"


class InvocationStatus(str, Enum):
    PENDING = "pending"
    WAITING_CONFIRMATION = "waiting_confirmation"
    READY = "ready"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    DENIED = "denied"
    UNKNOWN_EFFECT = "unknown_effect"
    PARTIAL_EFFECT = "partial_effect"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class AssetResultStatus(str, Enum):
    RESOLVED = "resolved"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    BLOCKED = "blocked"


class EffectStatus(str, Enum):
    NONE = "none"
    NOT_AUTHORIZED = "not_authorized"
    CONFIRMATION_REQUIRED = "confirmation_required"
    AUTHORIZED = "authorized"
    DISPATCHING = "dispatching"
    VERIFIED = "verified"
    DENIED = "denied"
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    FAILED = "failed"


class DeliveryStatus(str, Enum):
    NOT_READY = "not_ready"
    READY = "ready"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    SUPPRESSED = "suppressed"
    SHADOWED = "shadowed"
    FAILED_BEFORE_SEND = "failed_before_send"
    UNKNOWN = "unknown"


class TerminalOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_OUTCOME_PRECEDENCE: tuple[TerminalOutcome, ...] = (
    TerminalOutcome.BLOCKED,
    TerminalOutcome.CANCELLED,
    TerminalOutcome.TIMED_OUT,
    TerminalOutcome.FAILED,
    TerminalOutcome.UNSUPPORTED,
    TerminalOutcome.PARTIAL,
    TerminalOutcome.COMPLETED,
)
