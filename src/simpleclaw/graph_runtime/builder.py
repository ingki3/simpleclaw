"""Machine-readable transition table에서 V4 Recipe-first core graph를 compile한다."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langgraph.graph import END, START, StateGraph

from .nodes import (
    CoreCompletionCallbacks,
    CoreGraphState,
    CoreNodeCallbacks,
    callback_node,
    preserve_react_handoff,
    request_user_input_node,
)
from .routing import (
    CORE_TRANSITION_TABLES,
    GeneralRoute,
    RecipeMatchOutcome,
    RecipeResultOutcome,
    SolverOutcome,
    route_deep_research_result,
    route_general,
    route_react_result,
    route_recipe_match,
    route_recipe_result,
    validate_transition_totality,
)

LINEAR_CORE_EDGES = (
    (START, "normalize_ingress"),
    ("normalize_ingress", "load_existing_context"),
    ("load_existing_context", "analyze_request"),
    ("analyze_request", "snapshot_asset_catalogs"),
    ("snapshot_asset_catalogs", "match_recipe"),
    ("execute_existing_recipe", "assess_recipe_result"),
    ("simple_conversation", "compose_candidate"),
    ("react_subgraph", "assess_react_result"),
    ("preserve_react_handoff", "deep_research_subgraph"),
    ("deep_research_subgraph", "assess_deep_research_result"),
    ("compose_candidate", "final_composition"),
    ("final_composition", "prepare_delivery"),
    ("prepare_delivery", "commit_delivery"),
    ("commit_delivery", "persist_delivery_outcome"),
    ("persist_delivery_outcome", END),
)

CONDITIONAL_CORE_EDGES = {
    "match_recipe": CORE_TRANSITION_TABLES["recipe_match"],
    "assess_recipe_result": CORE_TRANSITION_TABLES["recipe_result"],
    "select_general_route": CORE_TRANSITION_TABLES["general_route"],
    "assess_react_result": CORE_TRANSITION_TABLES["react_result"],
    "assess_deep_research_result": CORE_TRANSITION_TABLES[
        "deep_research_result"
    ],
    "request_user_input": {
        "recipe": "execute_existing_recipe",
        "react": "react_subgraph",
        "deep_research": "deep_research_subgraph",
    },
}


def _require_enum(state: Mapping[str, Any], field: str, enum_type):
    value = state.get(field)
    if not isinstance(value, enum_type):
        raise TypeError(f"{field} must be a normalized {enum_type.__name__}")
    return value


def _recipe_match_route(state: Mapping[str, Any]) -> str:
    return route_recipe_match(
        _require_enum(state, "recipe_match", RecipeMatchOutcome)
    )


def _recipe_result_route(state: Mapping[str, Any]) -> str:
    return route_recipe_result(
        _require_enum(state, "recipe_result", RecipeResultOutcome)
    )


def _general_route(state: Mapping[str, Any]) -> str:
    return route_general(_require_enum(state, "general_route", GeneralRoute))


def _react_result_route(state: Mapping[str, Any]) -> str:
    return route_react_result(
        _require_enum(state, "solver_outcome", SolverOutcome)
    )


def _deep_research_result_route(state: Mapping[str, Any]) -> str:
    return route_deep_research_result(
        _require_enum(state, "solver_outcome", SolverOutcome)
    )


def _resume_route(state: Mapping[str, Any]) -> str:
    target = state.get("resume_target")
    if target not in {"recipe", "react", "deep_research"}:
        raise ValueError("resume_target must name the exact interrupted subgraph")
    return target


def validate_core_graph_tables() -> None:
    """compile 전에 unknown destination과 dead-end를 fail-closed로 차단한다."""
    validate_transition_totality()
    node_names = {
        "normalize_ingress",
        "load_existing_context",
        "analyze_request",
        "snapshot_asset_catalogs",
        "match_recipe",
        "execute_existing_recipe",
        "assess_recipe_result",
        "select_general_route",
        "simple_conversation",
        "react_subgraph",
        "assess_react_result",
        "preserve_react_handoff",
        "deep_research_subgraph",
        "assess_deep_research_result",
        "request_user_input",
        "compose_candidate",
        "final_composition",
        "prepare_delivery",
        "commit_delivery",
        "persist_delivery_outcome",
    }
    for source, destinations in CONDITIONAL_CORE_EDGES.items():
        if source not in node_names:
            raise ValueError(f"conditional source is unknown: {source}")
        if any(destination not in node_names for destination in destinations.values()):
            raise ValueError(f"conditional edge from {source} has unknown destination")
    outgoing = {source for source, _ in LINEAR_CORE_EDGES if source != START}
    outgoing.update(CONDITIONAL_CORE_EDGES)
    dead_ends = node_names - outgoing - {"persist_delivery_outcome"}
    if dead_ends:
        raise ValueError(f"core graph contains dead-end nodes: {sorted(dead_ends)}")


def build_core_graph(
    callbacks: CoreNodeCallbacks,
    completion: CoreCompletionCallbacks,
) -> StateGraph:
    """asset executor와 completion 경계를 주입한 reusable graph definition을 만든다."""
    validate_core_graph_tables()
    graph = StateGraph(CoreGraphState)
    callback_names = (
        "normalize_ingress",
        "load_existing_context",
        "analyze_request",
        "snapshot_asset_catalogs",
        "match_recipe",
        "execute_existing_recipe",
        "assess_recipe_result",
        "select_general_route",
        "simple_conversation",
        "react_subgraph",
        "assess_react_result",
        "deep_research_subgraph",
        "assess_deep_research_result",
        "compose_candidate",
    )
    for name in callback_names:
        graph.add_node(name, callback_node(getattr(callbacks, name)))
    for name in (
        "final_composition",
        "prepare_delivery",
        "commit_delivery",
        "persist_delivery_outcome",
    ):
        graph.add_node(name, callback_node(getattr(completion, name)))
    graph.add_node("preserve_react_handoff", preserve_react_handoff)
    graph.add_node(
        "request_user_input", request_user_input_node(callbacks.resume_user_input)
    )

    for source, destination in LINEAR_CORE_EDGES:
        graph.add_edge(source, destination)

    graph.add_conditional_edges("match_recipe", _recipe_match_route)
    graph.add_conditional_edges("assess_recipe_result", _recipe_result_route)
    graph.add_conditional_edges("select_general_route", _general_route)
    graph.add_conditional_edges(
        "assess_react_result",
        _react_result_route,
        {
            **{
                destination: destination
                for destination in set(
                    CORE_TRANSITION_TABLES["react_result"].values()
                )
            },
            "deep_research_subgraph": "preserve_react_handoff",
        },
    )
    graph.add_conditional_edges(
        "assess_deep_research_result", _deep_research_result_route
    )
    graph.add_conditional_edges(
        "request_user_input",
        _resume_route,
        dict(CONDITIONAL_CORE_EDGES["request_user_input"]),
    )
    return graph


def compile_core_graph(
    callbacks: CoreNodeCallbacks,
    completion: CoreCompletionCallbacks,
    *,
    checkpointer=None,
):
    """검증된 graph를 caller 소유 checkpointer와 compile한다."""
    return build_core_graph(callbacks, completion).compile(checkpointer=checkpointer)


validate_core_graph_tables()
