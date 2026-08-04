"""Recipe-first V4 core graph의 exhaustive routing policy.

이 모듈은 업무 payload를 해석하지 않는다. Adapter가 정규화한 control outcome만
받아 다음 node를 결정하며, 알려지지 않은 값은 외부 dispatch 전에 실패시킨다.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import TypeVar


class RoutingInvariantError(ValueError):
    """정규화되지 않았거나 table에 없는 route outcome이다."""


class RecipeMatchOutcome(str, Enum):
    APPLICABLE = "applicable"
    NO_MATCH = "no_match"
    INAPPLICABLE = "inapplicable"
    PARTIAL_COVERAGE = "partial_coverage"
    UNSAFE = "unsafe"


class GeneralRoute(str, Enum):
    SIMPLE_CONVERSATION = "simple_conversation"
    REACT = "react"
    DEEP_RESEARCH = "deep_research"


class SolverOutcome(str, Enum):
    RESOLVED = "resolved"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    UNRESOLVED = "unresolved"
    COMPLEXITY_INCREASED = "complexity_increased"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"
    PROVIDER_OUTAGE = "provider_outage"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    DENIED = "denied"
    SECURITY_DENIED = "security_denied"
    UNKNOWN_EFFECT = "unknown_effect"
    PARTIAL_EFFECT = "partial_effect"


class RecipeResultOutcome(str, Enum):
    RESOLVED = "resolved"
    NEEDS_INPUT = "needs_input"
    SAFE_MISS = "safe_miss"
    FAILED_BEFORE_EFFECT = "failed_before_effect"
    RECOVERABLE_READ_ONLY_FAILURE = "recoverable_read_only_failure"
    PARTIAL_EFFECT = "partial_effect"
    UNKNOWN_EFFECT = "unknown_effect"
    DENIED = "denied"
    SECURITY_DENIED = "security_denied"


RECIPE_MATCH_EDGES = MappingProxyType(
    {
        RecipeMatchOutcome.APPLICABLE: "execute_existing_recipe",
        RecipeMatchOutcome.NO_MATCH: "select_general_route",
        RecipeMatchOutcome.INAPPLICABLE: "select_general_route",
        RecipeMatchOutcome.PARTIAL_COVERAGE: "select_general_route",
        RecipeMatchOutcome.UNSAFE: "compose_candidate",
    }
)

RECIPE_RESULT_EDGES = MappingProxyType(
    {
        RecipeResultOutcome.RESOLVED: "compose_candidate",
        RecipeResultOutcome.NEEDS_INPUT: "request_user_input",
        RecipeResultOutcome.SAFE_MISS: "select_general_route",
        RecipeResultOutcome.FAILED_BEFORE_EFFECT: "select_general_route",
        RecipeResultOutcome.RECOVERABLE_READ_ONLY_FAILURE: "select_general_route",
        RecipeResultOutcome.PARTIAL_EFFECT: "compose_candidate",
        RecipeResultOutcome.UNKNOWN_EFFECT: "compose_candidate",
        RecipeResultOutcome.DENIED: "compose_candidate",
        RecipeResultOutcome.SECURITY_DENIED: "compose_candidate",
    }
)

GENERAL_ROUTE_EDGES = MappingProxyType(
    {
        GeneralRoute.SIMPLE_CONVERSATION: "simple_conversation",
        GeneralRoute.REACT: "react_subgraph",
        GeneralRoute.DEEP_RESEARCH: "deep_research_subgraph",
    }
)

REACT_RESULT_EDGES = MappingProxyType(
    {
        SolverOutcome.RESOLVED: "compose_candidate",
        SolverOutcome.PARTIAL: "compose_candidate",
        SolverOutcome.NEEDS_INPUT: "request_user_input",
        SolverOutcome.UNRESOLVED: "deep_research_subgraph",
        SolverOutcome.COMPLEXITY_INCREASED: "deep_research_subgraph",
        SolverOutcome.BUDGET_EXHAUSTED: "compose_candidate",
        SolverOutcome.FAILED: "compose_candidate",
        SolverOutcome.PROVIDER_OUTAGE: "compose_candidate",
        SolverOutcome.TIMED_OUT: "compose_candidate",
        SolverOutcome.CANCELLED: "compose_candidate",
        SolverOutcome.BLOCKED: "compose_candidate",
        SolverOutcome.DENIED: "compose_candidate",
        SolverOutcome.SECURITY_DENIED: "compose_candidate",
        SolverOutcome.UNKNOWN_EFFECT: "compose_candidate",
        SolverOutcome.PARTIAL_EFFECT: "compose_candidate",
    }
)

DEEP_RESEARCH_RESULT_EDGES = MappingProxyType(
    {
        outcome: (
            "request_user_input"
            if outcome is SolverOutcome.NEEDS_INPUT
            else "compose_candidate"
        )
        for outcome in SolverOutcome
    }
)

CORE_TRANSITION_TABLES = MappingProxyType(
    {
        "recipe_match": RECIPE_MATCH_EDGES,
        "recipe_result": RECIPE_RESULT_EDGES,
        "general_route": GENERAL_ROUTE_EDGES,
        "react_result": REACT_RESULT_EDGES,
        "deep_research_result": DEEP_RESEARCH_RESULT_EDGES,
    }
)

_OutcomeT = TypeVar("_OutcomeT", bound=Enum)


def _route(table: dict[_OutcomeT, str], outcome: _OutcomeT) -> str:
    try:
        return table[outcome]
    except (KeyError, TypeError) as exc:
        raise RoutingInvariantError(f"unmapped routing outcome: {outcome!r}") from exc


def route_recipe_match(outcome: RecipeMatchOutcome) -> str:
    return _route(RECIPE_MATCH_EDGES, outcome)


def route_recipe_result(outcome: RecipeResultOutcome) -> str:
    return _route(RECIPE_RESULT_EDGES, outcome)


def route_general(outcome: GeneralRoute) -> str:
    return _route(GENERAL_ROUTE_EDGES, outcome)


def route_react_result(outcome: SolverOutcome) -> str:
    return _route(REACT_RESULT_EDGES, outcome)


def route_deep_research_result(outcome: SolverOutcome) -> str:
    return _route(DEEP_RESEARCH_RESULT_EDGES, outcome)


def validate_transition_totality() -> None:
    """모든 enum 값이 정확히 하나의 유효한 목적지를 갖는지 검증한다."""
    expected = {
        "recipe_match": RecipeMatchOutcome,
        "recipe_result": RecipeResultOutcome,
        "general_route": GeneralRoute,
        "react_result": SolverOutcome,
        "deep_research_result": SolverOutcome,
    }
    valid_nodes = {
        "execute_existing_recipe",
        "select_general_route",
        "simple_conversation",
        "react_subgraph",
        "deep_research_subgraph",
        "request_user_input",
        "compose_candidate",
    }
    for name, enum_type in expected.items():
        table = CORE_TRANSITION_TABLES[name]
        if set(table) != set(enum_type):
            raise RoutingInvariantError(f"{name} transition table is not exhaustive")
        if any(destination not in valid_nodes for destination in table.values()):
            raise RoutingInvariantError(f"{name} contains an unknown destination")


validate_transition_totality()
