from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from simpleclaw.graph_runtime.builder import compile_core_graph
from simpleclaw.graph_runtime.nodes import CoreNodeCallbacks, RouteContinuityV1
from simpleclaw.graph_runtime.routing import (
    GeneralRoute,
    RecipeMatchOutcome,
    RecipeResultOutcome,
    SolverOutcome,
)


def _callbacks(
    *,
    recipe_match: RecipeMatchOutcome,
    recipe_result: RecipeResultOutcome = RecipeResultOutcome.RESOLVED,
    general_route: GeneralRoute = GeneralRoute.REACT,
    solver_outcome: SolverOutcome = SolverOutcome.RESOLVED,
    visits: list[str],
) -> CoreNodeCallbacks:
    def record(name, update=None):
        def run(_state):
            visits.append(name)
            return dict(update or {})

        return run

    return CoreNodeCallbacks(
        normalize_ingress=record("normalize_ingress", {"envelope": "request"}),
        load_existing_context=record("load_existing_context", {"context": {}}),
        analyze_request=record("analyze_request", {"analysis": {}}),
        snapshot_asset_catalogs=record("snapshot_asset_catalogs", {"catalog": {}}),
        match_recipe=record("match_recipe", {"recipe_match": recipe_match}),
        execute_existing_recipe=record(
            "execute_existing_recipe", {"normalized_result": "recipe"}
        ),
        assess_recipe_result=record(
            "assess_recipe_result", {"recipe_result": recipe_result}
        ),
        select_general_route=record(
            "select_general_route", {"general_route": general_route}
        ),
        simple_conversation=record(
            "simple_conversation", {"normalized_result": "simple"}
        ),
        react_subgraph=record("react_subgraph", {"normalized_result": "react"}),
        assess_react_result=record(
            "assess_react_result", {"solver_outcome": solver_outcome}
        ),
        deep_research_subgraph=record(
            "deep_research_subgraph", {"normalized_result": "deep"}
        ),
        assess_deep_research_result=record(
            "assess_deep_research_result", {"solver_outcome": solver_outcome}
        ),
        compose_candidate=record(
            "compose_candidate", {"composition_candidate": "draft"}
        ),
        resume_user_input=lambda _state, _control: {},
    )


def _initial_state() -> dict:
    return {
        "ingress": "hello",
        "observations": ("observation-1",),
        "attempted_signatures": ("attempt-1",),
        "remaining_graph_steps": 7,
        "remaining_asset_calls": 3,
        "remaining_llm_calls": 2,
        "remaining_tokens": 800,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=1),
        "cancellation_token": "cancel-1",
    }


@pytest.mark.asyncio
async def test_recipe_resolved_precedes_every_general_route() -> None:
    visits = []
    graph = compile_core_graph(
        _callbacks(recipe_match=RecipeMatchOutcome.APPLICABLE, visits=visits)
    )
    result = await graph.ainvoke(_initial_state())

    assert result["composition_candidate"] == "draft"
    assert "execute_existing_recipe" in visits
    assert "select_general_route" not in visits
    assert "react_subgraph" not in visits


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route,expected",
    [
        (GeneralRoute.SIMPLE_CONVERSATION, "simple_conversation"),
        (GeneralRoute.REACT, "react_subgraph"),
        (GeneralRoute.DEEP_RESEARCH, "deep_research_subgraph"),
    ],
)
async def test_safe_recipe_miss_reaches_exactly_one_general_route(route, expected) -> None:
    visits = []
    graph = compile_core_graph(
        _callbacks(
            recipe_match=RecipeMatchOutcome.NO_MATCH,
            general_route=route,
            visits=visits,
        )
    )
    await graph.ainvoke(_initial_state())

    selected = {
        "simple_conversation",
        "react_subgraph",
        "deep_research_subgraph",
    } & set(visits)
    assert selected == {expected}


@pytest.mark.asyncio
async def test_react_escalation_preserves_control_state() -> None:
    visits = []
    callbacks = _callbacks(
        recipe_match=RecipeMatchOutcome.NO_MATCH,
        solver_outcome=SolverOutcome.UNRESOLVED,
        visits=visits,
    )
    seen: list[RouteContinuityV1] = []

    def deep(state):
        visits.append("deep_research_subgraph")
        seen.append(state["route_continuity"])
        return {"normalized_result": "deep"}

    callbacks = replace(callbacks, deep_research_subgraph=deep)
    graph = compile_core_graph(callbacks)
    await graph.ainvoke(_initial_state())

    continuity = seen[0]
    assert continuity.observations == ("observation-1",)
    assert continuity.attempted_signatures == ("attempt-1",)
    assert continuity.remaining_tokens == 800
    assert continuity.cancellation_token == "cancel-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", [item for item in SolverOutcome if item is not SolverOutcome.NEEDS_INPUT]
)
async def test_deep_research_every_outcome_reaches_artifact_or_interrupt(outcome) -> None:
    visits = []
    graph = compile_core_graph(
        _callbacks(
            recipe_match=RecipeMatchOutcome.NO_MATCH,
            general_route=GeneralRoute.DEEP_RESEARCH,
            solver_outcome=outcome,
            visits=visits,
        )
    )
    result = await graph.ainvoke(_initial_state())

    assert result["composition_candidate"] == "draft"
    assert visits[-1] == "compose_candidate"
