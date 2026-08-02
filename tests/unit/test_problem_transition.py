from simpleclaw.agent.problem_transition import ProblemTransitionBuilder
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    ExecutionMode,
    GoalResolutionState,
    GoalStatus,
    ResolutionBudget,
)


def test_empty_result_transitions_to_explanation_without_complex_escalation() -> None:
    transition = ProblemTransitionBuilder().build(
        goal=GoalResolutionState(
            original_goal="오늘 롯데 경기 스코어와 상태를 알려준다",
            status=GoalStatus.NEEDS_EXPLANATION,
            resolved_claims=(),
            unresolved_claims=("game_status_reason",),
        ),
        previous_question="오늘 롯데 스코어는?",
        result=AssetResult(
            asset_type="skill",
            asset_name="naver-sports-skill",
            status=AssetExecutionStatus.EMPTY,
            next_questions=("오늘 롯데 경기가 예정되어 있었는가?",),
        ),
        required_claims=("game_status_reason",),
        fallback_allows_complex=True,
        budget=ResolutionBudget(max_steps=4),
    )
    assert transition is not None
    assert transition.recommended_mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    assert transition.original_goal.startswith("오늘 롯데")

