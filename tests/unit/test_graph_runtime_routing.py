from __future__ import annotations

import pytest

from simpleclaw.graph_runtime.builder import validate_core_graph_tables
from simpleclaw.graph_runtime.routing import (
    CORE_TRANSITION_TABLES,
    DEEP_RESEARCH_RESULT_EDGES,
    REACT_RESULT_EDGES,
    GeneralRoute,
    RecipeMatchOutcome,
    RecipeResultOutcome,
    SolverOutcome,
    route_general,
    route_react_result,
    route_recipe_match,
    route_recipe_result,
)


def test_recipe_is_always_evaluated_before_exact_three_way_selector() -> None:
    assert route_recipe_match(RecipeMatchOutcome.APPLICABLE) == (
        "execute_existing_recipe"
    )
    for miss in (
        RecipeMatchOutcome.NO_MATCH,
        RecipeMatchOutcome.INAPPLICABLE,
        RecipeMatchOutcome.PARTIAL_COVERAGE,
    ):
        assert route_recipe_match(miss) == "select_general_route"
    assert {route_general(route) for route in GeneralRoute} == {
        "simple_conversation",
        "react_subgraph",
        "deep_research_subgraph",
    }


@pytest.mark.parametrize(
    "outcome",
    [
        SolverOutcome.UNRESOLVED,
        SolverOutcome.COMPLEXITY_INCREASED,
        SolverOutcome.BUDGET_EXHAUSTED,
    ],
)
def test_only_information_or_complexity_gap_escalates_react(outcome) -> None:
    assert route_react_result(outcome) == "deep_research_subgraph"


@pytest.mark.parametrize(
    "outcome",
    [
        SolverOutcome.FAILED,
        SolverOutcome.PROVIDER_OUTAGE,
        SolverOutcome.TIMED_OUT,
        SolverOutcome.CANCELLED,
        SolverOutcome.BLOCKED,
        SolverOutcome.DENIED,
        SolverOutcome.SECURITY_DENIED,
        SolverOutcome.UNKNOWN_EFFECT,
        SolverOutcome.PARTIAL_EFFECT,
    ],
)
def test_operational_and_unsafe_failures_never_escalate_react(outcome) -> None:
    assert route_react_result(outcome) == "compose_candidate"


def test_recipe_unsafe_result_never_falls_back() -> None:
    unsafe = (
        RecipeResultOutcome.PARTIAL_EFFECT,
        RecipeResultOutcome.UNKNOWN_EFFECT,
        RecipeResultOutcome.DENIED,
        RecipeResultOutcome.SECURITY_DENIED,
    )
    assert all(route_recipe_result(item) == "compose_candidate" for item in unsafe)


def test_every_core_transition_table_is_exhaustive_and_dead_end_free() -> None:
    validate_core_graph_tables()
    assert set(REACT_RESULT_EDGES) == set(SolverOutcome)
    assert set(DEEP_RESEARCH_RESULT_EDGES) == set(SolverOutcome)
    assert set(CORE_TRANSITION_TABLES) == {
        "recipe_match",
        "recipe_result",
        "general_route",
        "react_result",
        "deep_research_result",
    }
