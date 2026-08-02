import asyncio
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.evidence_investigation import EvidenceInvestigationController
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    ExecutionMode,
    GoalStatus,
    ProblemTransition,
    ResolutionBudget,
)
from simpleclaw.agent.turn_plan import AssetRef


@pytest.mark.asyncio
async def test_no_progress_stops_without_repeating_signature() -> None:
    execute = AsyncMock(
        return_value=AssetResult(
            asset_type="skill",
            asset_name="lookup",
            status=AssetExecutionStatus.EMPTY,
            unresolved_claims=("status",),
        )
    )
    transition = ProblemTransition(
        original_goal="상태를 알려준다",
        previous_question="상태?",
        triggering_observation="empty",
        goal_status=GoalStatus.NEEDS_EXPLANATION,
        unresolved_gap="status",
        next_question="경기 상태를 확인한다",
        required_claims=("status",),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="needs_explanation",
    )
    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(AssetRef("skill", "lookup"),),
        budget=ResolutionBudget(max_steps=4, max_tool_calls=4),
        ledger=ResolutionLedger(),
    )
    assert outcome.stop_reason == "no_progress"
    assert execute.await_count == 1


@pytest.mark.asyncio
async def test_token_budget_is_cumulative_across_gap_attempts() -> None:
    execute = AsyncMock(
        return_value=AssetResult(
            asset_type="skill",
            asset_name="lookup",
            status=AssetExecutionStatus.PARTIAL_SUCCESS,
            resolved_claims=("score",),
            unresolved_claims=("rank",),
            next_questions=("순위를 확인한다",),
            tokens_used=5,
        )
    )
    transition = ProblemTransition(
        original_goal="점수와 순위를 알려준다",
        previous_question="점수와 순위?",
        triggering_observation="partial",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="점수를 확인한다",
        required_claims=("score", "rank"),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="partial_capability",
    )
    ledger = ResolutionLedger()
    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(AssetRef("skill", "lookup"),),
        budget=ResolutionBudget(max_steps=4, max_tool_calls=4, token_budget=5),
        ledger=ledger,
    )

    assert outcome.stop_reason == "budget_exhausted"
    assert execute.await_count == 1
    assert ledger.tokens_used == 5


@pytest.mark.asyncio
async def test_investigation_deadline_cancels_supporting_asset() -> None:
    async def slow_execute(*_args: object) -> AssetResult:
        await asyncio.sleep(0.05)
        return AssetResult(
            asset_type="skill",
            asset_name="lookup",
            status=AssetExecutionStatus.COMPLETED,
            resolved_claims=("status",),
        )

    transition = ProblemTransition(
        original_goal="상태를 알려준다",
        previous_question="상태?",
        triggering_observation="partial",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="status",
        next_question="상태를 확인한다",
        required_claims=("status",),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="partial_capability",
    )
    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=slow_execute
    ).run(
        transition,
        supporting_assets=(AssetRef("skill", "lookup"),),
        budget=ResolutionBudget.from_seconds(max_seconds=0.01, max_steps=2),
        ledger=ResolutionLedger(),
    )

    assert outcome.stop_reason == "terminal"
    assert outcome.last_result is not None
    assert outcome.last_result.limitations == ("deadline_exhausted",)
