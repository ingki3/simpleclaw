from simpleclaw.agent.goal_resolution import GoalResolver
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    GoalStatus,
)


def test_empty_observation_can_require_explanation() -> None:
    result = AssetResult(
        asset_type="skill",
        asset_name="sports",
        status=AssetExecutionStatus.EMPTY,
        unresolved_claims=("game_status_reason",),
        next_questions=("오늘 경기가 예정되어 있었는가?",),
    )
    goal = GoalResolver().evaluate(
        original_goal="오늘 경기 상태를 알려준다",
        required_claims=("game_status_reason",),
        result=result,
        ledger=ResolutionLedger(),
    )
    assert goal.status is GoalStatus.NEEDS_EXPLANATION


def test_unknown_side_effect_blocks_goal() -> None:
    goal = GoalResolver().evaluate(
        original_goal="일정을 만든다",
        required_claims=("event_created",),
        result=AssetResult(
            asset_type="skill",
            asset_name="calendar",
            status=AssetExecutionStatus.UNKNOWN_EFFECT,
            side_effect=True,
        ),
        ledger=ResolutionLedger(),
    )
    assert goal.status is GoalStatus.BLOCKED

