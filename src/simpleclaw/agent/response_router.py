"""Lightweight response route classifier for SimpleClaw turns.

The router is intentionally conservative. It sends only structurally complex
factual/scenario questions into ComplexFactWorkflow. Simple chat, normal tool
use, and single current-fact lookup stay on the existing ToolLoopRunner path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ResponseRoute(str, Enum):
    """Top-level execution paths for ordinary user turns."""

    STANDARD_TOOL_LOOP = "standard_tool_loop"
    CURRENT_FACT_GUARDED_LOOP = "current_fact_guarded_loop"
    COMPLEX_FACT_WORKFLOW = "complex_fact_workflow"


@dataclass(frozen=True)
class RouteDecision:
    """Structured route decision used by AgentOrchestrator."""

    route: ResponseRoute
    complexity_score: int
    reasons: list[str] = field(default_factory=list)
    needs_current_facts: bool = False
    needs_rules: bool = False
    needs_remaining_variables: bool = False
    needs_calculation: bool = False
    needs_comparison_or_conditions: bool = False
    needs_conflict_resolution: bool = False


_SMALLTALK = ("안녕", "고마워", "감사", "hello", "hi", "thanks")
_CURRENT_FACT_CUES = (
    "오늘",
    "현재",
    "지금",
    "실시간",
    "최신",
    "방금",
    "마감",
    "결과",
    "예보",
)
_DOMAIN_CURRENT_CUES = (
    "날씨",
    "주가",
    "환율",
    "뉴스",
    "속보",
    "경기",
    "스코어",
    "코스피",
    "코스닥",
    "순위",
    "승점",
)
_RULE_CUES = (
    "규칙",
    "조건",
    "기준",
    "적용",
    "자격",
    "진출",
    "탈락",
    "우승",
    "정책",
    "요건",
    "16강",
    "8강",
    "티켓",
)
_REMAINING_VARIABLE_CUES = (
    "남은",
    "앞으로",
    "경우의 수",
    "가능성",
    "시나리오",
    "어떻게 되면",
    "아직",
    "될 수",
)
_CALCULATION_CUES = (
    "계산",
    "확률",
    "몇",
    "순위",
    "비교",
    "경우의 수",
    "점수",
    "승점",
    "차이",
    "얼마",
)
_CONDITION_CUES = (
    "이면",
    "라면",
    "조건별",
    "비교",
    "대상",
    "그룹",
    "표",
    "랭킹",
    "내 상황",
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def classify_response_route(
    text: str,
    prior_context: str = "",
    *,
    route_threshold: int = 3,
) -> RouteDecision:
    """Classify a turn into a cheap execution path.

    This is not intended to be perfect. It should avoid false positives for the
    heavy workflow while catching structurally complex questions that require
    several independent evidence slots before answering.
    """

    normalized = " ".join((text or "").split())
    if not normalized:
        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["empty"])

    if len(normalized) <= 20 and _contains_any(normalized, _SMALLTALK):
        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["smalltalk"])

    combined = f"{prior_context[-1200:]}\n{normalized}" if prior_context else normalized
    needs_remaining = _contains_any(combined, _REMAINING_VARIABLE_CUES)
    needs_calculation = _contains_any(combined, _CALCULATION_CUES)
    needs_conditions = _contains_any(combined, _CONDITION_CUES)
    needs_rules = _contains_any(combined, _RULE_CUES)

    # Scenario wording generally implies an external current state and governing
    # rule even if the user does not say "현재" or "규칙" explicitly.
    if needs_remaining and (needs_calculation or needs_conditions):
        needs_rules = True
        needs_conditions = True

    needs_current = _contains_any(combined, _CURRENT_FACT_CUES) or _contains_any(
        combined, _DOMAIN_CURRENT_CUES
    )
    if needs_remaining:
        needs_current = True

    reasons: list[str] = []
    score = 0
    for flag, reason in [
        (needs_current, "current_facts"),
        (needs_rules, "rules"),
        (needs_remaining, "remaining_variables"),
        (needs_calculation, "calculation"),
        (needs_conditions, "conditions_or_comparison"),
    ]:
        if flag:
            score += 1
            reasons.append(reason)

    threshold = max(1, int(route_threshold))
    complex_shape = score >= threshold or (
        needs_rules and (needs_remaining or needs_conditions) and not _is_single_current_fact(combined)
    )
    if complex_shape:
        return RouteDecision(
            ResponseRoute.COMPLEX_FACT_WORKFLOW,
            score,
            reasons,
            needs_current_facts=needs_current,
            needs_rules=needs_rules,
            needs_remaining_variables=needs_remaining,
            needs_calculation=needs_calculation,
            needs_comparison_or_conditions=needs_conditions,
            needs_conflict_resolution=needs_current and (needs_rules or needs_remaining),
        )

    if needs_current:
        return RouteDecision(
            ResponseRoute.CURRENT_FACT_GUARDED_LOOP,
            score,
            reasons or ["current_fact_guard"],
            needs_current_facts=True,
        )

    return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, score, reasons or ["default"])


def _is_single_current_fact(text: str) -> bool:
    """Return True for simple quote/weather/news lookups that should stay cheap."""

    simple_lookup_cues = ("날씨", "주가", "환율", "뉴스", "속보", "얼마")
    return _contains_any(text, simple_lookup_cues) and not _contains_any(
        text,
        _REMAINING_VARIABLE_CUES + _RULE_CUES + _CONDITION_CUES,
    )
