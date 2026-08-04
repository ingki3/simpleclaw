from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from simpleclaw.graph_runtime.builder import compile_core_graph
from simpleclaw.graph_runtime.checkpoint import InterruptRequestV1, UserDecisionV1
from simpleclaw.graph_runtime.nodes import CoreNodeCallbacks
from simpleclaw.graph_runtime.routing import (
    RecipeMatchOutcome,
    RecipeResultOutcome,
)


def _request(kind: str, *, resume_node: str = "recipe") -> InterruptRequestV1:
    values = {
        "interrupt_id": f"{kind}-1",
        "kind": kind,
        "question": "추가 입력이 필요합니다.",
        "resume_node": resume_node,
        "checkpoint_thread_id": "turn:interrupt-test",
        "checkpoint_version": 1,
        "contract_version": "1",
        "contract_schema_hash": "contract-hash",
        "catalog_fingerprint": "catalog-hash",
        "plan_id": "plan-1",
        "plan_revision": 1,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
    }
    if kind == "confirmation":
        values.update(
            invocation_id="invoke-1",
            payload_hash="payload-hash",
            definition_fingerprint="definition-hash",
        )
    return InterruptRequestV1(**values)


def _decision(request: InterruptRequestV1) -> UserDecisionV1:
    values = {
        "interrupt_id": request.interrupt_id,
        "checkpoint_thread_id": request.checkpoint_thread_id,
        "checkpoint_version": request.checkpoint_version,
        "contract_version": request.contract_version,
        "contract_schema_hash": request.contract_schema_hash,
        "catalog_fingerprint": request.catalog_fingerprint,
        "plan_id": request.plan_id,
        "plan_revision": request.plan_revision,
        "invocation_id": request.invocation_id,
        "payload_hash": request.payload_hash,
        "definition_fingerprint": request.definition_fingerprint,
    }
    if request.kind == "confirmation":
        values["confirmed"] = True
    else:
        values["text"] = "서울 기준"
    return UserDecisionV1(**values)


def _interrupt_callbacks(kind: str, counters: dict[str, int]) -> CoreNodeCallbacks:
    request = _request(kind)

    def empty(_state):
        return {}

    def analyze(_state):
        counters["planner"] += 1
        return {"planner_calls": counters["planner"]}

    def execute(state):
        if "resume_control" not in state:
            return {"interrupt_request": request}
        if kind == "confirmation":
            counters["dispatch"] += 1
        return {"normalized_result": "resolved"}

    def assess(state):
        outcome = (
            RecipeResultOutcome.RESOLVED
            if "resume_control" in state
            else RecipeResultOutcome.NEEDS_INPUT
        )
        return {"recipe_result": outcome}

    def resume(state, control):
        if kind == "clarification":
            counters["planner"] += 1
            return {"planner_calls": counters["planner"]}
        assert control.payload_hash == request.payload_hash
        return {"planner_calls": state["planner_calls"]}

    return CoreNodeCallbacks(
        normalize_ingress=empty,
        load_existing_context=empty,
        analyze_request=analyze,
        snapshot_asset_catalogs=empty,
        match_recipe=lambda _state: {
            "recipe_match": RecipeMatchOutcome.APPLICABLE
        },
        execute_existing_recipe=execute,
        assess_recipe_result=assess,
        select_general_route=empty,
        simple_conversation=empty,
        react_subgraph=empty,
        assess_react_result=empty,
        deep_research_subgraph=empty,
        assess_deep_research_result=empty,
        compose_candidate=lambda _state: {"composition_candidate": "draft"},
        resume_user_input=resume,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,expected_planner_calls,expected_dispatch_calls",
    [("clarification", 2, 0), ("confirmation", 1, 1)],
)
async def test_interrupt_resumes_exact_recipe_control_point(
    kind, expected_planner_calls, expected_dispatch_calls
) -> None:
    counters = {"planner": 0, "dispatch": 0}
    graph = compile_core_graph(
        _interrupt_callbacks(kind, counters), checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "turn:interrupt-test"}}

    interrupted = await graph.ainvoke({"ingress": "request"}, config)
    assert interrupted["__interrupt__"]
    assert counters["dispatch"] == 0

    request = _request(kind)
    resumed = await graph.ainvoke(
        Command(resume=_decision(request).model_dump(mode="json")), config
    )

    assert resumed["composition_candidate"] == "draft"
    assert resumed["resume_target"] == "recipe"
    assert counters["planner"] == expected_planner_calls
    assert counters["dispatch"] == expected_dispatch_calls
    if kind == "confirmation":
        assert resumed["resume_control"].next_revision == 1
        assert resumed["resume_control"].payload_hash == "payload-hash"
    else:
        assert resumed["resume_control"].next_revision == 2
