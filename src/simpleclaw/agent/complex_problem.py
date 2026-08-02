"""Dependency/conflict/calculation 기반 complex problem controller."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from simpleclaw.agent.resolution_ledger import ResolutionLedger, attempt_signature
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    ResolutionBudget,
)
from simpleclaw.agent.turn_plan import AssetRef

ComplexNodeExecutor = Callable[
    ["ProblemNode", AssetRef, ResolutionLedger],
    Awaitable[AssetResult],
]


@dataclass(frozen=True)
class ProblemNode:
    node_id: str
    claim: str
    question: str
    dependencies: tuple[str, ...] = ()
    allowed_assets: tuple[AssetRef, ...] = ()


@dataclass
class ComplexProblemState:
    original_goal: str
    nodes: list[ProblemNode]
    ledger: ResolutionLedger
    resolved_node_ids: set[str] = field(default_factory=set)
    blocked_node_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ComplexProblemOutcome:
    state: ComplexProblemState
    success: bool
    stop_reason: str
    limitations: tuple[str, ...] = ()


class ComplexProblemController:
    """Ready node만 처리하고 cycle/repeat/no-progress에서 종료한다."""

    def __init__(self, *, execute_node: ComplexNodeExecutor) -> None:
        self._execute_node = execute_node

    async def run(
        self,
        state: ComplexProblemState,
        *,
        budget: ResolutionBudget,
    ) -> ComplexProblemOutcome:
        steps = 0
        calls = 0
        while budget.snapshot(steps_used=steps, tool_calls_used=calls).can_continue:
            pending = [
                node
                for node in state.nodes
                if node.node_id not in state.resolved_node_ids
                and node.node_id not in state.blocked_node_ids
            ]
            if not pending:
                return ComplexProblemOutcome(state, True, "resolved")
            ready = next(
                (
                    node
                    for node in pending
                    if set(node.dependencies) <= state.resolved_node_ids
                ),
                None,
            )
            if ready is None:
                return ComplexProblemOutcome(
                    state,
                    False,
                    "dependency_cycle",
                    ("unresolved_dependency_cycle",),
                )
            if not ready.allowed_assets:
                state.blocked_node_ids.add(ready.node_id)
                continue
            asset = ready.allowed_assets[0]
            signature = attempt_signature(
                question=ready.question,
                asset_type=asset.asset_type,
                asset_name=asset.name,
                parameters={"node_id": ready.node_id},
            )
            if not state.ledger.record_attempt(signature):
                return ComplexProblemOutcome(
                    state,
                    False,
                    "repeated_attempt_signature",
                    (f"repeated_node:{ready.node_id}",),
                )
            result = await self._execute_node(ready, asset, state.ledger)
            if not state.ledger.asset_results or state.ledger.asset_results[-1] is not result:
                state.ledger.append_asset_result(result)
            steps += 1
            calls += 1
            if ready.claim in result.resolved_claims:
                state.resolved_node_ids.add(ready.node_id)
                continue
            if result.status in {
                AssetExecutionStatus.DENIED,
                AssetExecutionStatus.FAILED_TERMINAL,
                AssetExecutionStatus.UNKNOWN_EFFECT,
            }:
                state.blocked_node_ids.add(ready.node_id)
                return ComplexProblemOutcome(
                    state,
                    False,
                    "terminal",
                    result.limitations,
                )
            return ComplexProblemOutcome(
                state,
                False,
                "no_progress",
                (f"unresolved_node:{ready.node_id}",),
            )
        return ComplexProblemOutcome(
            state,
            False,
            "budget_exhausted",
            ("resolution_budget_exhausted",),
        )

