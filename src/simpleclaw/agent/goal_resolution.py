"""Asset 실행 결과와 원래 사용자 목표를 독립 평가한다."""

from __future__ import annotations

from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    GoalResolutionState,
    GoalStatus,
)


class GoalResolver:
    """Domain-neutral claim coverage 기반 목표 평가기."""

    def evaluate(
        self,
        *,
        original_goal: str,
        required_claims: tuple[str, ...],
        result: AssetResult,
        ledger: ResolutionLedger,
    ) -> GoalResolutionState:
        resolved = set(result.resolved_claims)
        resolved.update(
            item.claim_id
            for item in ledger.evidence
            if item.usable and item.claim_id
        )
        required = tuple(dict.fromkeys(required_claims))
        unresolved = tuple(
            claim for claim in required if claim not in resolved
        )
        for claim in result.unresolved_claims:
            if claim and claim not in unresolved:
                unresolved += (claim,)

        if result.status is AssetExecutionStatus.NEEDS_INPUT:
            status = GoalStatus.NEEDS_USER_INPUT
        elif result.side_effect and result.status in {
            AssetExecutionStatus.PARTIAL_SUCCESS,
            AssetExecutionStatus.UNKNOWN_EFFECT,
        }:
            status = GoalStatus.BLOCKED
        elif result.status in {
            AssetExecutionStatus.DENIED,
            AssetExecutionStatus.FAILED_TERMINAL,
            AssetExecutionStatus.UNSUPPORTED,
        }:
            status = GoalStatus.BLOCKED
        elif result.status in {
            AssetExecutionStatus.EMPTY,
            AssetExecutionStatus.NOT_FOUND,
        } and (result.next_questions or unresolved):
            status = GoalStatus.NEEDS_EXPLANATION
        elif not unresolved and (
            required or result.status in {
                AssetExecutionStatus.COMPLETED,
                AssetExecutionStatus.EMPTY,
                AssetExecutionStatus.NOT_FOUND,
            }
        ):
            status = GoalStatus.RESOLVED
        elif resolved:
            status = GoalStatus.PARTIALLY_RESOLVED
        else:
            status = GoalStatus.UNRESOLVED

        blockers: tuple[str, ...] = ()
        if status is GoalStatus.BLOCKED:
            blockers = result.limitations or (result.status.value,)
        explanation = unresolved if status is GoalStatus.NEEDS_EXPLANATION else ()
        return GoalResolutionState(
            original_goal=original_goal,
            status=status,
            resolved_claims=tuple(sorted(resolved)),
            unresolved_claims=unresolved,
            explanation_needed=explanation,
            blockers=blockers,
        )

