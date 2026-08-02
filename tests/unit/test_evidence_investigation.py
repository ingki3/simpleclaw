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

