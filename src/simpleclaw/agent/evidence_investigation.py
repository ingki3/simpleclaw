"""명시적 evidence gap 하나씩 닫는 bounded investigation loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from simpleclaw.agent.goal_resolution import GoalResolver
from simpleclaw.agent.resolution_ledger import ResolutionLedger, attempt_signature
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    GoalResolutionState,
    GoalStatus,
    ProblemTransition,
    ResolutionBudget,
)
from simpleclaw.agent.turn_plan import AssetRef

SupportingAssetExecutor = Callable[
    [AssetRef, str, ResolutionLedger],
    Awaitable[AssetResult],
]


@dataclass(frozen=True)
class InvestigationState:
    original_goal: str
    current_question: str
    observed_facts: tuple[str, ...]
    unresolved_claims: tuple[str, ...]
    selected_gap: str
    next_investigation_question: str


@dataclass(frozen=True)
class InvestigationOutcome:
    goal: GoalResolutionState
    ledger: ResolutionLedger
    state: InvestigationState
    stop_reason: str
    last_result: AssetResult | None = None


class EvidenceInvestigationController:
    """Selected supporting assets만 실행하며 반복/no-progress를 차단한다."""

    def __init__(
        self,
        *,
        execute_supporting_asset: SupportingAssetExecutor,
        goal_resolver: GoalResolver | None = None,
    ) -> None:
        self._execute = execute_supporting_asset
        self._goal_resolver = goal_resolver or GoalResolver()

    async def run(
        self,
        transition: ProblemTransition,
        *,
        supporting_assets: tuple[AssetRef, ...],
        budget: ResolutionBudget,
        ledger: ResolutionLedger,
    ) -> InvestigationOutcome:
        unresolved = transition.required_claims or (transition.unresolved_gap,)
        prior_result = ledger.asset_results[-1] if ledger.asset_results else None
        prior_goal: GoalResolutionState | None = None
        if prior_result is not None:
            prior_goal = self._goal_resolver.evaluate(
                original_goal=transition.original_goal,
                required_claims=unresolved,
                result=prior_result,
                ledger=ledger,
            )
            if prior_goal.status is GoalStatus.RESOLVED:
                state = InvestigationState(
                    original_goal=transition.original_goal,
                    current_question=transition.next_question,
                    observed_facts=prior_goal.resolved_claims,
                    unresolved_claims=(),
                    selected_gap="",
                    next_investigation_question=transition.next_question,
                )
                return InvestigationOutcome(
                    goal=prior_goal,
                    ledger=ledger,
                    state=state,
                    stop_reason="resolved_from_existing_evidence",
                    last_result=prior_result,
                )
            unresolved = prior_goal.unresolved_claims or unresolved
        current_question = transition.next_question
        selected_gap = unresolved[0]
        state = InvestigationState(
            original_goal=transition.original_goal,
            current_question=current_question,
            observed_facts=prior_goal.resolved_claims if prior_goal is not None else (),
            unresolved_claims=unresolved,
            selected_gap=selected_gap,
            next_investigation_question=current_question,
        )
        unresolved_goal = prior_goal or GoalResolutionState(
            original_goal=transition.original_goal,
            status=GoalStatus.UNRESOLVED,
            resolved_claims=(),
            unresolved_claims=unresolved,
        )
        if not supporting_assets:
            return InvestigationOutcome(
                goal=unresolved_goal,
                ledger=ledger,
                state=state,
                stop_reason="supporting_asset_missing",
            )

        last_result: AssetResult | None = None
        terminal_assets: set[tuple[str, str]] = set()
        asset_index = 0
        while budget.snapshot(
            steps_used=ledger.steps_used,
            tool_calls_used=ledger.tool_calls_used,
            tokens_used=ledger.tokens_used,
        ).can_continue:
            selected_asset: AssetRef | None = None
            for offset in range(len(supporting_assets)):
                candidate_index = (asset_index + offset) % len(supporting_assets)
                candidate = supporting_assets[candidate_index]
                if (candidate.asset_type, candidate.name) in terminal_assets:
                    continue
                selected_asset = candidate
                asset_index = (candidate_index + 1) % len(supporting_assets)
                break
            if selected_asset is None:
                return InvestigationOutcome(
                    goal=unresolved_goal,
                    ledger=ledger,
                    state=state,
                    stop_reason="terminal",
                    last_result=last_result,
                )
            asset = selected_asset
            signature = attempt_signature(
                question=current_question,
                asset_type=asset.asset_type,
                asset_name=asset.name,
                parameters={"selected_gap": selected_gap},
            )
            if not ledger.record_attempt(signature):
                return InvestigationOutcome(
                    goal=unresolved_goal,
                    ledger=ledger,
                    state=state,
                    stop_reason="repeated_attempt_signature",
                    last_result=last_result,
                )
            try:
                last_result = await budget.wait_for(
                    self._execute(asset, current_question, ledger)
                )
            except TimeoutError:
                last_result = AssetResult(
                    asset_type=asset.asset_type,
                    asset_name=asset.name,
                    status=AssetExecutionStatus.FAILED_TERMINAL,
                    unresolved_claims=(selected_gap,),
                    limitations=("deadline_exhausted",),
                )
            ledger.record_usage(
                steps=1,
                tool_calls=1,
                tokens=last_result.tokens_used,
            )
            if not ledger.asset_results or ledger.asset_results[-1] is not last_result:
                ledger.append_asset_result(last_result)
            goal = self._goal_resolver.evaluate(
                original_goal=transition.original_goal,
                required_claims=transition.required_claims,
                result=last_result,
                ledger=ledger,
            )
            observed = tuple(
                dict.fromkeys((*state.observed_facts, *last_result.resolved_claims))
            )
            remaining = goal.unresolved_claims
            next_gap = remaining[0] if remaining else ""
            next_question = next(iter(last_result.next_questions), current_question)
            state = InvestigationState(
                original_goal=transition.original_goal,
                current_question=current_question,
                observed_facts=observed,
                unresolved_claims=remaining,
                selected_gap=next_gap,
                next_investigation_question=next_question,
            )
            if goal.status is GoalStatus.RESOLVED:
                return InvestigationOutcome(goal, ledger, state, "resolved", last_result)
            if goal.status is GoalStatus.NEEDS_USER_INPUT:
                return InvestigationOutcome(goal, ledger, state, "needs_user_input", last_result)
            if goal.status is GoalStatus.BLOCKED or last_result.status in {
                AssetExecutionStatus.DENIED,
                AssetExecutionStatus.UNKNOWN_EFFECT,
            }:
                return InvestigationOutcome(goal, ledger, state, "terminal", last_result)
            if last_result.status is AssetExecutionStatus.FAILED_TERMINAL:
                if last_result.side_effect or "deadline_exhausted" in last_result.limitations:
                    return InvestigationOutcome(goal, ledger, state, "terminal", last_result)
                terminal_assets.add((asset.asset_type, asset.name))
                unresolved_goal = goal
                current_question = next_question
                selected_gap = next_gap or selected_gap
                continue
            if next_question == current_question and not last_result.resolved_claims:
                return InvestigationOutcome(goal, ledger, state, "no_progress", last_result)
            unresolved_goal = goal
            current_question = next_question
            selected_gap = next_gap or selected_gap

        return InvestigationOutcome(
            unresolved_goal,
            ledger,
            state,
            "budget_exhausted",
            last_result,
        )
