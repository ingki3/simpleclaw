"""미해결 목표 gap을 다음 bounded 조사 문제로 전환한다."""

from __future__ import annotations

from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    ExecutionMode,
    GoalResolutionState,
    GoalStatus,
    ProblemTransition,
    ResolutionBudget,
    decide_complex_escalation,
)


class ProblemTransitionBuilder:
    """Goal 상태와 asset hint만 사용해 domain-neutral transition을 만든다."""

    def build(
        self,
        *,
        goal: GoalResolutionState,
        previous_question: str,
        result: AssetResult,
        required_claims: tuple[str, ...],
        fallback_allows_complex: bool,
        budget: ResolutionBudget,
        steps_used: int = 0,
        tool_calls_used: int = 0,
    ) -> ProblemTransition | None:
        if goal.status is GoalStatus.RESOLVED:
            return None
        gap = next(
            iter(goal.unresolved_claims or goal.explanation_needed),
            "unresolved_goal",
        )
        next_question = next(iter(result.next_questions), "")
        if not next_question:
            next_question = f"다음 미해결 항목을 확인한다: {gap}"

        if goal.status is GoalStatus.NEEDS_USER_INPUT:
            mode = ExecutionMode.CLARIFY
            reason = "needs_user_input"
        elif goal.status is GoalStatus.BLOCKED or (
            result.side_effect
            and result.status in {
                AssetExecutionStatus.PARTIAL_SUCCESS,
                AssetExecutionStatus.UNKNOWN_EFFECT,
            }
        ):
            return None
        else:
            escalation = decide_complex_escalation(
                result=result,
                fallback_allows_complex=fallback_allows_complex,
                budget=budget,
                steps_used=steps_used,
                tool_calls_used=tool_calls_used,
            )
            if escalation.escalate:
                mode = ExecutionMode.RESOLVE_COMPLEX_PROBLEM
                reason = f"complex:{escalation.reason}"
            else:
                mode = ExecutionMode.ANSWER_WITH_EVIDENCE
                reason = (
                    "needs_explanation"
                    if goal.status is GoalStatus.NEEDS_EXPLANATION
                    else "unresolved_gap"
                )

        return ProblemTransition(
            original_goal=goal.original_goal,
            previous_question=previous_question,
            triggering_observation=result.status.value,
            goal_status=goal.status,
            unresolved_gap=gap,
            next_question=next_question,
            required_claims=tuple(dict.fromkeys(required_claims or (gap,))),
            recommended_mode=mode,
            transition_reason=reason,
        )

