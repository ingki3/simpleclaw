"""V4 core graph node wrappers.

각 node는 callback을 한 번 호출해 append/update를 반환하는 얇은 경계다. Recipe와
Skill의 업무 payload binding은 후속 adapter가 소유한다.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from .checkpoint import (
    InterruptRequestV1,
    ResumeControlV1,
    UserDecisionV1,
    validate_resume,
)
from .routing import (
    GeneralRoute,
    RecipeMatchOutcome,
    RecipeResultOutcome,
    SolverOutcome,
)

NodeUpdate = dict[str, Any]
NodeCallback = Callable[
    [Mapping[str, Any]], NodeUpdate | Awaitable[NodeUpdate]
]
ResumeCallback = Callable[
    [Mapping[str, Any], ResumeControlV1], NodeUpdate | Awaitable[NodeUpdate]
]


class RouteContinuityV1(BaseModel):
    """ReAct에서 DeepResearch로 넘길 때 손실되면 안 되는 control state다."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    observations: tuple[Any, ...] = ()
    attempted_signatures: tuple[str, ...] = ()
    remaining_graph_steps: int = Field(ge=0)
    remaining_asset_calls: int = Field(ge=0)
    remaining_llm_calls: int = Field(ge=0)
    remaining_tokens: int = Field(ge=0)
    deadline_at: datetime
    cancellation_token: str = Field(min_length=1)


class CoreGraphState(TypedDict, total=False):
    """Adapter-owned opaque values와 core-owned routing 값을 분리한 state다."""

    ingress: object
    envelope: object
    context: object
    analysis: object
    catalog: object
    recipe_match: RecipeMatchOutcome
    recipe_result: RecipeResultOutcome
    general_route: GeneralRoute
    solver_outcome: SolverOutcome
    normalized_result: object
    composition_candidate: object
    interrupt_request: InterruptRequestV1
    resume_control: ResumeControlV1
    resume_target: str
    route_continuity: RouteContinuityV1
    observations: tuple[Any, ...]
    attempted_signatures: tuple[str, ...]
    remaining_graph_steps: int
    remaining_asset_calls: int
    remaining_llm_calls: int
    remaining_tokens: int
    deadline_at: datetime
    cancellation_token: str
    planner_calls: int


async def _invoke(callback: NodeCallback, state: Mapping[str, Any]) -> NodeUpdate:
    update = callback(state)
    if inspect.isawaitable(update):
        update = await update
    if not isinstance(update, dict):
        raise TypeError("graph callback must return a dict update")
    return update


async def _invoke_resume(
    callback: ResumeCallback,
    state: Mapping[str, Any],
    control: ResumeControlV1,
) -> NodeUpdate:
    update = callback(state, control)
    if inspect.isawaitable(update):
        update = await update
    if not isinstance(update, dict):
        raise TypeError("resume callback must return a dict update")
    return update


@dataclass(frozen=True, slots=True)
class CoreNodeCallbacks:
    normalize_ingress: NodeCallback
    load_existing_context: NodeCallback
    analyze_request: NodeCallback
    snapshot_asset_catalogs: NodeCallback
    match_recipe: NodeCallback
    execute_existing_recipe: NodeCallback
    assess_recipe_result: NodeCallback
    select_general_route: NodeCallback
    simple_conversation: NodeCallback
    react_subgraph: NodeCallback
    assess_react_result: NodeCallback
    deep_research_subgraph: NodeCallback
    assess_deep_research_result: NodeCallback
    compose_candidate: NodeCallback
    resume_user_input: ResumeCallback


def callback_node(callback: NodeCallback) -> NodeCallback:
    """동기/비동기 adapter callback을 동일한 async LangGraph node로 만든다."""

    async def node(state: Mapping[str, Any]) -> NodeUpdate:
        return await _invoke(callback, state)

    return node


def request_user_input_node(callback: ResumeCallback) -> NodeCallback:
    """동일 checkpoint에서 interrupt하고 검증된 exact control point로 재개한다."""

    async def node(state: Mapping[str, Any]) -> NodeUpdate:
        request = state.get("interrupt_request")
        if not isinstance(request, InterruptRequestV1):
            raise TypeError("request_user_input requires InterruptRequestV1")
        # LangGraph의 generic serializer가 tuple을 list로 복원할 수 있으므로
        # interrupt payload를 내보내기 전에 strict contract로 다시 수화한다.
        request = InterruptRequestV1.model_validate(
            {**request.__dict__, "choices": tuple(request.choices)}
        )
        raw_decision = interrupt(request.model_dump(mode="json"))
        decision = UserDecisionV1.model_validate(raw_decision)
        control = validate_resume(request, decision)
        update = await _invoke_resume(callback, state, control)
        if "resume_control" in update and update["resume_control"] != control:
            raise ValueError("resume callback cannot replace validated resume control")
        return {
            **update,
            "resume_control": control,
            "resume_target": control.resume_node,
        }

    return node


def preserve_react_handoff(state: Mapping[str, Any]) -> NodeUpdate:
    """ReAct escalation의 관찰·시도·budget/deadline/cancel identity를 동결한다."""
    return {
        "route_continuity": RouteContinuityV1(
            observations=tuple(state.get("observations", ())),
            attempted_signatures=tuple(state.get("attempted_signatures", ())),
            remaining_graph_steps=state["remaining_graph_steps"],
            remaining_asset_calls=state["remaining_asset_calls"],
            remaining_llm_calls=state["remaining_llm_calls"],
            remaining_tokens=state["remaining_tokens"],
            deadline_at=state["deadline_at"],
            cancellation_token=state["cancellation_token"],
        )
    }
