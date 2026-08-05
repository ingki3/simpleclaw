"""V4 graph 런타임의 공통 lifecycle과 outcome 어휘를 정의한다.

이 enum들은 흐름과 side effect 상태만 표현한다. 자산이 소유한 payload 의미는
자산 계약에만 속하므로 이 모듈에 추가하지 않는다.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import TypeVar


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


class StatusTransitionError(ValueError):
    """상태 축의 허용되지 않은 전이를 나타낸다."""


_StatusT = TypeVar("_StatusT", bound=Enum)


def _frozen_edges(
    edges: dict[_StatusT, frozenset[_StatusT]],
) -> MappingProxyType[_StatusT, frozenset[_StatusT]]:
    return MappingProxyType(edges)


LIFECYCLE_TRANSITIONS = _frozen_edges(
    {
        LifecycleStatus.NEW: frozenset({LifecycleStatus.ACTIVE}),
        LifecycleStatus.ACTIVE: frozenset(
            {LifecycleStatus.INTERRUPTED, LifecycleStatus.TERMINAL}
        ),
        LifecycleStatus.INTERRUPTED: frozenset(
            {LifecycleStatus.ACTIVE, LifecycleStatus.TERMINAL}
        ),
        LifecycleStatus.TERMINAL: frozenset(),
    }
)

PLAN_STATUS_TRANSITIONS = _frozen_edges(
    {
        PlanStatus.ABSENT: frozenset({PlanStatus.PROPOSED}),
        PlanStatus.PROPOSED: frozenset(
            {
                PlanStatus.VALID,
                PlanStatus.REPAIRABLE,
                PlanStatus.NEEDS_INPUT,
                PlanStatus.INVALID,
            }
        ),
        PlanStatus.REPAIRABLE: frozenset(
            {PlanStatus.VALID, PlanStatus.NEEDS_INPUT, PlanStatus.INVALID}
        ),
        PlanStatus.VALID: frozenset(),
        PlanStatus.NEEDS_INPUT: frozenset(),
        PlanStatus.INVALID: frozenset(),
    }
)

INVOCATION_STATUS_TRANSITIONS = _frozen_edges(
    {
        InvocationStatus.PENDING: frozenset(
            {InvocationStatus.READY, InvocationStatus.WAITING_CONFIRMATION}
        ),
        InvocationStatus.WAITING_CONFIRMATION: frozenset(
            {InvocationStatus.READY, InvocationStatus.DENIED}
        ),
        InvocationStatus.READY: frozenset({InvocationStatus.DISPATCHING}),
        InvocationStatus.DISPATCHING: frozenset(
            {
                InvocationStatus.SUCCEEDED,
                InvocationStatus.FAILED_RETRYABLE,
                InvocationStatus.FAILED_TERMINAL,
                InvocationStatus.DENIED,
                InvocationStatus.UNKNOWN_EFFECT,
                InvocationStatus.PARTIAL_EFFECT,
                InvocationStatus.TIMED_OUT,
                InvocationStatus.CANCELLED,
            }
        ),
        InvocationStatus.SUCCEEDED: frozenset(),
        InvocationStatus.FAILED_RETRYABLE: frozenset(),
        InvocationStatus.FAILED_TERMINAL: frozenset(),
        InvocationStatus.DENIED: frozenset(),
        InvocationStatus.UNKNOWN_EFFECT: frozenset(),
        InvocationStatus.PARTIAL_EFFECT: frozenset(),
        InvocationStatus.TIMED_OUT: frozenset(),
        InvocationStatus.CANCELLED: frozenset(),
    }
)

EFFECT_STATUS_TRANSITIONS = _frozen_edges(
    {
        EffectStatus.NONE: frozenset({EffectStatus.NOT_AUTHORIZED}),
        EffectStatus.NOT_AUTHORIZED: frozenset(
            {EffectStatus.CONFIRMATION_REQUIRED}
        ),
        EffectStatus.CONFIRMATION_REQUIRED: frozenset(
            {EffectStatus.AUTHORIZED, EffectStatus.DENIED}
        ),
        EffectStatus.AUTHORIZED: frozenset({EffectStatus.DISPATCHING}),
        EffectStatus.DISPATCHING: frozenset(
            {
                EffectStatus.VERIFIED,
                EffectStatus.DENIED,
                EffectStatus.UNKNOWN,
                EffectStatus.PARTIAL,
                EffectStatus.FAILED,
            }
        ),
        EffectStatus.VERIFIED: frozenset(),
        EffectStatus.DENIED: frozenset(),
        EffectStatus.UNKNOWN: frozenset(),
        EffectStatus.PARTIAL: frozenset(),
        EffectStatus.FAILED: frozenset(),
    }
)

DELIVERY_STATUS_TRANSITIONS = _frozen_edges(
    {
        DeliveryStatus.NOT_READY: frozenset({DeliveryStatus.READY}),
        DeliveryStatus.READY: frozenset({DeliveryStatus.DISPATCHING}),
        DeliveryStatus.DISPATCHING: frozenset(
            {
                DeliveryStatus.DELIVERED,
                DeliveryStatus.SUPPRESSED,
                DeliveryStatus.SHADOWED,
                DeliveryStatus.FAILED_BEFORE_SEND,
                DeliveryStatus.UNKNOWN,
            }
        ),
        DeliveryStatus.FAILED_BEFORE_SEND: frozenset({DeliveryStatus.READY}),
        DeliveryStatus.DELIVERED: frozenset(),
        DeliveryStatus.SUPPRESSED: frozenset(),
        DeliveryStatus.SHADOWED: frozenset(),
        DeliveryStatus.UNKNOWN: frozenset(),
    }
)

LEGAL_TRANSITION_TABLES = MappingProxyType(
    {
        LifecycleStatus: LIFECYCLE_TRANSITIONS,
        PlanStatus: PLAN_STATUS_TRANSITIONS,
        InvocationStatus: INVOCATION_STATUS_TRANSITIONS,
        EffectStatus: EFFECT_STATUS_TRANSITIONS,
        DeliveryStatus: DELIVERY_STATUS_TRANSITIONS,
    }
)


def is_legal_transition(current: _StatusT, target: _StatusT) -> bool:
    """동일 상태 no-op 또는 machine-readable table에 있는 전이만 허용한다."""
    if type(current) is not type(target):
        return False
    if current == target:
        return True
    table = LEGAL_TRANSITION_TABLES.get(type(current))
    return table is not None and target in table[current]


def require_legal_transition(current: _StatusT, target: _StatusT) -> None:
    """불법 전이를 외부 동작 전에 fail-closed로 거부한다."""
    if not is_legal_transition(current, target):
        raise StatusTransitionError(
            f"illegal {type(current).__name__} transition: "
            f"{current.value} -> {target.value}"
        )


def select_terminal_outcome(
    outcomes: tuple[TerminalOutcome, ...] | list[TerminalOutcome],
    *,
    effect_status: EffectStatus = EffectStatus.NONE,
) -> TerminalOutcome | None:
    """강한 실패가 약한 성공으로 가려지지 않도록 lattice를 적용한다."""
    candidates = set(outcomes)
    if effect_status in {EffectStatus.UNKNOWN, EffectStatus.PARTIAL}:
        candidates.add(TerminalOutcome.BLOCKED)
    return next(
        (outcome for outcome in TERMINAL_OUTCOME_PRECEDENCE if outcome in candidates),
        None,
    )


# Conditional router가 참조하는 exhaustive하고 machine-readable한 edge 표다.
# 새 enum member가 추가되면 totality test가 누락을 즉시 검출한다.
CONDITIONAL_EDGE_TABLES = MappingProxyType(
    {
        AssetResultStatus: MappingProxyType(
            {
                AssetResultStatus.RESOLVED: "compose_candidate",
                AssetResultStatus.PARTIAL: "compose_candidate",
                AssetResultStatus.UNRESOLVED: "select_fallback",
                AssetResultStatus.NEEDS_INPUT: "interrupt_for_clarification",
                AssetResultStatus.FAILED: "compose_candidate",
                AssetResultStatus.BLOCKED: "compose_candidate",
            }
        ),
        DeliveryStatus: MappingProxyType(
            {
                DeliveryStatus.NOT_READY: "prepare_delivery",
                DeliveryStatus.READY: "commit_delivery",
                DeliveryStatus.DISPATCHING: "commit_delivery",
                DeliveryStatus.DELIVERED: "persist_by_delivery_outcome",
                DeliveryStatus.SUPPRESSED: "persist_by_delivery_outcome",
                DeliveryStatus.SHADOWED: "persist_by_delivery_outcome",
                DeliveryStatus.FAILED_BEFORE_SEND: "delivery_retry_policy",
                DeliveryStatus.UNKNOWN: "persist_by_delivery_outcome",
            }
        ),
        TerminalOutcome: MappingProxyType(
            {outcome: "compose_candidate" for outcome in TerminalOutcome}
        ),
    }
)
