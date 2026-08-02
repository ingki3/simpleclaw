"""Capability fast path → Goal Resolution → 4 mode → common validator 조립."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from simpleclaw.agent.capability_executor import CapabilityExecutor
from simpleclaw.agent.goal_resolution import GoalResolver
from simpleclaw.agent.problem_transition import ProblemTransitionBuilder
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    CapabilityCoverage,
    ExecutionMode,
    GoalResolutionState,
    GoalStatus,
    ProblemTransition,
    ResolutionBudget,
)
from simpleclaw.agent.response_finalizer import ResponseFinalizer
from simpleclaw.agent.result_validator import (
    CommonResultValidator,
    ValidationDecision,
)
from simpleclaw.agent.turn_plan import UnifiedTurnPlan

ModeHandler = Callable[
    [UnifiedTurnPlan, ProblemTransition | None, ResolutionLedger, ResolutionBudget],
    Awaitable[AssetResult | str | None],
]


@dataclass(frozen=True)
class ResolutionOutcome:
    """Top-level controller의 검증 가능한 최종 결과."""

    text: str
    goal: GoalResolutionState
    ledger: ResolutionLedger
    validation: ValidationDecision
    mode: ExecutionMode
    asset_result: AssetResult | None = None
    transition: ProblemTransition | None = None
    stop_reason: str = ""


class ResolutionController:
    """Planner의 immutable decision을 재분류 없이 실행한다."""

    def __init__(
        self,
        *,
        capability_executor: CapabilityExecutor,
        clarify: ModeHandler | None = None,
        direct_answer: ModeHandler | None = None,
        answer_with_evidence: ModeHandler | None = None,
        resolve_complex_problem: ModeHandler | None = None,
        goal_resolver: GoalResolver | None = None,
        transition_builder: ProblemTransitionBuilder | None = None,
        validator: CommonResultValidator | None = None,
        finalizer: ResponseFinalizer | None = None,
        complex_escalation_enabled: bool = False,
    ) -> None:
        self._capability_executor = capability_executor
        self._handlers = {
            ExecutionMode.CLARIFY: clarify,
            ExecutionMode.DIRECT_ANSWER: direct_answer,
            ExecutionMode.ANSWER_WITH_EVIDENCE: answer_with_evidence,
            ExecutionMode.RESOLVE_COMPLEX_PROBLEM: resolve_complex_problem,
        }
        self._goal_resolver = goal_resolver or GoalResolver()
        self._transition_builder = transition_builder or ProblemTransitionBuilder()
        self._validator = validator or CommonResultValidator()
        self._finalizer = finalizer or ResponseFinalizer()
        self._complex_escalation_enabled = complex_escalation_enabled

    async def resolve(
        self,
        plan: UnifiedTurnPlan,
        *,
        budget: ResolutionBudget,
    ) -> ResolutionOutcome:
        ledger = ResolutionLedger()
        required_claims = plan.fact_check.required_claims
        transition: ProblemTransition | None = None
        asset_result: AssetResult | None = None
        mode = plan.execution.mode

        if (
            plan.capability.coverage is CapabilityCoverage.FULL
            and plan.capability.primary_asset is not None
        ):
            asset_result = await self._capability_executor.execute(
                plan,
                budget=budget,
                ledger=ledger,
            )
            if not ledger.asset_results or ledger.asset_results[-1] is not asset_result:
                ledger.append_asset_result(asset_result)
            goal = self._goal_resolver.evaluate(
                original_goal=plan.context.standalone_question,
                required_claims=required_claims,
                result=asset_result,
                ledger=ledger,
            )
            transition = self._transition_builder.build(
                goal=goal,
                previous_question=plan.context.standalone_question,
                result=asset_result,
                required_claims=required_claims,
                fallback_allows_complex=(
                    self._complex_escalation_enabled
                    and ExecutionMode.RESOLVE_COMPLEX_PROBLEM
                    in plan.capability.fallback_modes
                ),
                budget=budget,
                steps_used=1,
                tool_calls_used=1,
            )
            if goal.status is GoalStatus.RESOLVED:
                return self._final_outcome(
                    plan=plan,
                    goal=goal,
                    ledger=ledger,
                    mode=mode,
                    asset_result=asset_result,
                    transition=None,
                    draft=str(asset_result.data.get("text") or ""),
                    stop_reason="exact_asset_resolved",
                )
            if transition is None:
                return self._final_outcome(
                    plan=plan,
                    goal=goal,
                    ledger=ledger,
                    mode=mode,
                    asset_result=asset_result,
                    transition=None,
                    stop_reason="exact_asset_blocked",
                )
            mode = transition.recommended_mode
        else:
            goal = GoalResolutionState(
                original_goal=plan.context.standalone_question,
                status=GoalStatus.UNRESOLVED,
                resolved_claims=(),
                unresolved_claims=required_claims,
            )

        if mode is ExecutionMode.CLARIFY:
            validation = ValidationDecision(False, (), required_claims, (), "none")
            question = plan.clarification.question or (
                transition.next_question if transition is not None else "추가 정보가 필요합니다."
            )
            return ResolutionOutcome(
                text=self._finalizer.finalize(validation, clarify_question=question),
                goal=GoalResolutionState(
                    original_goal=plan.context.standalone_question,
                    status=GoalStatus.NEEDS_USER_INPUT,
                    resolved_claims=(),
                    unresolved_claims=required_claims,
                ),
                ledger=ledger,
                validation=validation,
                mode=mode,
                asset_result=asset_result,
                transition=transition,
                stop_reason="clarify",
            )

        handler = self._handlers.get(mode)
        if (
            mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM
            and not self._complex_escalation_enabled
        ):
            handler = None
        draft = ""
        if handler is not None:
            handled = await handler(plan, transition, ledger, budget)
            if isinstance(handled, AssetResult):
                asset_result = handled
                if not ledger.asset_results or ledger.asset_results[-1] is not handled:
                    ledger.append_asset_result(handled)
                goal = self._goal_resolver.evaluate(
                    original_goal=plan.context.standalone_question,
                    required_claims=required_claims,
                    result=handled,
                    ledger=ledger,
                )
                draft = str(handled.data.get("text") or "")
            elif isinstance(handled, str):
                draft = handled
                if not required_claims and mode is ExecutionMode.DIRECT_ANSWER:
                    goal = GoalResolutionState(
                        original_goal=plan.context.standalone_question,
                        status=GoalStatus.RESOLVED,
                        resolved_claims=(),
                        unresolved_claims=(),
                    )
        elif mode is ExecutionMode.DIRECT_ANSWER and not required_claims:
            goal = GoalResolutionState(
                original_goal=plan.context.standalone_question,
                status=GoalStatus.RESOLVED,
                resolved_claims=(),
                unresolved_claims=(),
            )
        else:
            asset_result = asset_result or AssetResult(
                asset_type="controller",
                asset_name=mode.value,
                status=AssetExecutionStatus.UNSUPPORTED,
                unresolved_claims=required_claims,
                limitations=("mode_handler_unavailable",),
            )
            goal = self._goal_resolver.evaluate(
                original_goal=plan.context.standalone_question,
                required_claims=required_claims,
                result=asset_result,
                ledger=ledger,
            )

        return self._final_outcome(
            plan=plan,
            goal=goal,
            ledger=ledger,
            mode=mode,
            asset_result=asset_result,
            transition=transition,
            draft=draft,
            stop_reason="mode_completed",
        )

    def _final_outcome(
        self,
        *,
        plan: UnifiedTurnPlan,
        goal: GoalResolutionState,
        ledger: ResolutionLedger,
        mode: ExecutionMode,
        asset_result: AssetResult | None,
        transition: ProblemTransition | None,
        draft: str = "",
        stop_reason: str,
    ) -> ResolutionOutcome:
        validation = self._validator.validate(
            goal=goal,
            ledger=ledger,
            required_claims=plan.fact_check.required_claims,
        )
        return ResolutionOutcome(
            text=self._finalizer.finalize(validation, draft=draft),
            goal=goal,
            ledger=ledger,
            validation=validation,
            mode=mode,
            asset_result=asset_result,
            transition=transition,
            stop_reason=stop_reason,
        )
