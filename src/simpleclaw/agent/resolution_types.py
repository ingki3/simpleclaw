"""Capability-first 문제 해결 흐름의 domain-neutral typed contract.

Asset 실행 관찰과 사용자 목표의 해결 상태를 의도적으로 분리한다. 이 모듈은
도메인별 source/query/parser를 알지 않으며, controller가 공유하는 상태와 유한
budget만 정의한다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionMode(str, Enum):
    """Capability fast path 이후 선택할 네 개의 상위 실행 mode."""

    CLARIFY = "clarify"
    DIRECT_ANSWER = "direct_answer"
    ANSWER_WITH_EVIDENCE = "answer_with_evidence"
    RESOLVE_COMPLEX_PROBLEM = "resolve_complex_problem"


class CapabilityCoverage(str, Enum):
    """Planner가 선언한 capability의 원래 목표 coverage."""

    FULL = "full_coverage"
    PARTIAL = "partial_coverage"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    NEEDS_INPUT = "needs_input"
    NEEDS_CONFIRMATION = "needs_confirmation"


class AssetExecutionStatus(str, Enum):
    """Asset 실행에서 관찰한 결과. 목표 해결 여부는 포함하지 않는다."""

    COMPLETED = "completed"
    EMPTY = "empty"
    NOT_FOUND = "not_found"
    NEEDS_INPUT = "needs_input"
    UNSUPPORTED = "unsupported"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    DENIED = "denied"
    PARTIAL_SUCCESS = "partial_success"
    UNKNOWN_EFFECT = "unknown_effect"


class GoalStatus(str, Enum):
    """원래 사용자 목표에 대한 독립적인 해결 상태."""

    RESOLVED = "resolved"
    PARTIALLY_RESOLVED = "partially_resolved"
    UNRESOLVED = "unresolved"
    NEEDS_EXPLANATION = "needs_explanation"
    NEEDS_USER_INPUT = "needs_user_input"
    BLOCKED = "blocked"


class ComplexitySignal(str, Enum):
    """Complex problem controller 진입을 정당화하는 명시적 신호."""

    DEPENDENCY_GRAPH = "dependency_graph"
    EVIDENCE_CONFLICT = "evidence_conflict"
    CALCULATION_OR_RULE = "calculation_or_rule"
    BRANCHING_PLAN = "branching_plan"
    ORDERED_CAPABILITY_COMPOSITION = "ordered_capability_composition"


@dataclass(frozen=True)
class AssetResult:
    """Skill/Recipe/tool 실행을 정규화한 typed observation."""

    asset_type: str
    asset_name: str
    status: AssetExecutionStatus
    data: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[dict[str, Any], ...] = ()
    resolved_claims: tuple[str, ...] = ()
    unresolved_claims: tuple[str, ...] = ()
    next_questions: tuple[str, ...] = ()
    complexity_signals: tuple[ComplexitySignal, ...] = ()
    side_effect: bool = False
    effect_id: str = ""
    retryable: bool = False
    limitations: tuple[str, ...] = ()
    tokens_used: int = 0

    def __post_init__(self) -> None:
        """Side-effect 불명 상태가 자동 재시도 가능해지지 않게 fail-closed한다."""
        if self.side_effect and self.status in {
            AssetExecutionStatus.PARTIAL_SUCCESS,
            AssetExecutionStatus.UNKNOWN_EFFECT,
        }:
            object.__setattr__(self, "retryable", False)
        if (
            not isinstance(self.tokens_used, int)
            or isinstance(self.tokens_used, bool)
            or self.tokens_used < 0
        ):
            object.__setattr__(self, "tokens_used", 0)


@dataclass(frozen=True)
class GoalResolutionState:
    """원래 목표와 claim coverage를 기준으로 평가한 해결 상태."""

    original_goal: str
    status: GoalStatus
    resolved_claims: tuple[str, ...]
    unresolved_claims: tuple[str, ...]
    explanation_needed: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProblemTransition:
    """미해결 gap 하나를 다음 내부 조사 문제로 전환한 기록."""

    original_goal: str
    previous_question: str
    triggering_observation: str
    goal_status: GoalStatus
    unresolved_gap: str
    next_question: str
    required_claims: tuple[str, ...]
    recommended_mode: ExecutionMode
    transition_reason: str


@dataclass(frozen=True)
class ResolutionBudget:
    """운영 설정에서 주입되는 유한 실행 budget.

    ``None``은 schema/shadow 단계의 미설정을 표현한다. canary/primary 활성화
    여부는 config gate가 별도로 검사하며 이 타입이 임의 production 기본값을
    만들지 않는다.
    """

    max_steps: int | None = None
    max_tool_calls: int | None = None
    deadline_monotonic: float | None = None
    token_budget: int | None = None

    @classmethod
    def from_seconds(
        cls,
        *,
        max_seconds: float | None,
        max_steps: int | None = None,
        max_tool_calls: int | None = None,
        token_budget: int | None = None,
    ) -> ResolutionBudget:
        """상대 초 설정을 monotonic deadline으로 변환한다."""
        deadline = None
        if max_seconds is not None:
            deadline = time.monotonic() + max(0.0, max_seconds)
        return cls(
            max_steps=max_steps,
            max_tool_calls=max_tool_calls,
            deadline_monotonic=deadline,
            token_budget=token_budget,
        )

    def snapshot(
        self,
        *,
        steps_used: int = 0,
        tool_calls_used: int = 0,
        tokens_used: int = 0,
        now_monotonic: float | None = None,
    ) -> BudgetSnapshot:
        """현재 소비량에서 계속/승격 가능 여부를 계산한다."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        reasons: list[str] = []
        if self.max_steps is not None and steps_used >= self.max_steps:
            reasons.append("max_steps")
        if self.max_tool_calls is not None and tool_calls_used >= self.max_tool_calls:
            reasons.append("max_tool_calls")
        if self.deadline_monotonic is not None and now >= self.deadline_monotonic:
            reasons.append("deadline")
        if self.token_budget is not None and tokens_used >= self.token_budget:
            reasons.append("token_budget")
        return BudgetSnapshot(
            steps_used=max(0, steps_used),
            tool_calls_used=max(0, tool_calls_used),
            tokens_used=max(0, tokens_used),
            can_continue=not reasons,
            can_escalate=not reasons,
            stop_reasons=tuple(reasons),
        )

    def remaining_seconds(self, *, now_monotonic: float | None = None) -> float | None:
        """In-flight await에 적용할 남은 deadline 초를 반환한다."""
        if self.deadline_monotonic is None:
            return None
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return max(0.0, self.deadline_monotonic - now)

    async def wait_for(self, awaitable: Awaitable[Any]) -> Any:
        """Deadline이 있으면 실행 중인 await도 동일 budget으로 중단한다."""
        remaining = self.remaining_seconds()
        if remaining is None:
            return await awaitable
        if remaining <= 0:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise TimeoutError("resolution deadline exhausted")
        return await asyncio.wait_for(awaitable, timeout=remaining)


@dataclass(frozen=True)
class BudgetSnapshot:
    """한 시점의 budget 소비량과 종료 이유."""

    steps_used: int
    tool_calls_used: int
    tokens_used: int
    can_continue: bool
    can_escalate: bool
    stop_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EscalationDecision:
    """Complex mode 승격 gate의 결과."""

    escalate: bool
    reason: str


def decide_complex_escalation(
    *,
    result: AssetResult,
    fallback_allows_complex: bool,
    budget: ResolutionBudget,
    steps_used: int = 0,
    tool_calls_used: int = 0,
    tokens_used: int = 0,
) -> EscalationDecision:
    """신호·policy·budget을 모두 만족할 때만 complex 승격한다."""
    if result.side_effect and result.status in {
        AssetExecutionStatus.PARTIAL_SUCCESS,
        AssetExecutionStatus.UNKNOWN_EFFECT,
    }:
        return EscalationDecision(False, "side_effect_uncertain")
    if result.status in {
        AssetExecutionStatus.EMPTY,
        AssetExecutionStatus.NOT_FOUND,
        AssetExecutionStatus.DENIED,
        AssetExecutionStatus.FAILED_TERMINAL,
        AssetExecutionStatus.UNKNOWN_EFFECT,
    }:
        return EscalationDecision(False, f"status:{result.status.value}")
    if not result.complexity_signals:
        return EscalationDecision(False, "complexity_signal_missing")
    if not fallback_allows_complex:
        return EscalationDecision(False, "fallback_policy_denied")
    snapshot = budget.snapshot(
        steps_used=steps_used,
        tool_calls_used=tool_calls_used,
        tokens_used=tokens_used,
    )
    if not snapshot.can_escalate:
        return EscalationDecision(False, "budget_exhausted")
    return EscalationDecision(True, result.complexity_signals[0].value)
