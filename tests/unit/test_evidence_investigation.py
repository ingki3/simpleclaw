import asyncio
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.evidence_investigation import EvidenceInvestigationController
from simpleclaw.agent.resolution_ledger import ResolutionLedger, attempt_signature
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


@pytest.mark.asyncio
async def test_partial_side_effect_terminal_does_not_start_fallback_asset() -> None:
    execute = AsyncMock(
        return_value=AssetResult(
            asset_type="skill",
            asset_name="first",
            status=AssetExecutionStatus.FAILED_TERMINAL,
            resolved_claims=("score",),
            unresolved_claims=("rank",),
            next_questions=("순위를 확인한다",),
            side_effect=True,
        )
    )
    transition = ProblemTransition(
        original_goal="점수와 순위를 알려준다",
        previous_question="점수와 순위?",
        triggering_observation="failed_terminal",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="점수를 확인한다",
        required_claims=("score", "rank"),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="side_effect_terminal",
    )

    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(
            AssetRef("skill", "first"),
            AssetRef("skill", "second"),
        ),
        budget=ResolutionBudget(max_steps=3, max_tool_calls=3),
        ledger=ResolutionLedger(),
    )

    assert outcome.stop_reason == "terminal"
    assert outcome.goal.status is GoalStatus.BLOCKED
    assert execute.await_count == 1
    assert execute.await_args.args[0].name == "first"


@pytest.mark.asyncio
async def test_read_only_terminal_advances_to_next_distinct_supporting_asset() -> None:
    async def execute(asset: AssetRef, *_args: object) -> AssetResult:
        if asset.name == "first":
            return AssetResult(
                asset_type="skill",
                asset_name=asset.name,
                status=AssetExecutionStatus.FAILED_TERMINAL,
                unresolved_claims=("score",),
            )
        return AssetResult(
            asset_type="skill",
            asset_name=asset.name,
            status=AssetExecutionStatus.COMPLETED,
            resolved_claims=("score",),
            evidence=(
                {
                    "claim_id": "score",
                    "value": "70",
                    "source_url": "https://example.test/score",
                    "fresh": True,
                },
            ),
        )

    execute_mock = AsyncMock(side_effect=execute)
    transition = ProblemTransition(
        original_goal="현재 경기 결과를 알려준다",
        previous_question="현재 스코어는?",
        triggering_observation="failed_terminal",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="현재 스코어를 확인한다",
        required_claims=("score",),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="read_only_terminal_fallback",
    )
    ledger = ResolutionLedger()

    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute_mock
    ).run(
        transition,
        supporting_assets=(
            AssetRef("skill", "first"),
            AssetRef("skill", "second"),
        ),
        budget=ResolutionBudget(max_steps=3, max_tool_calls=3),
        ledger=ledger,
    )

    assert outcome.stop_reason == "resolved"
    assert outcome.goal.status is GoalStatus.RESOLVED
    assert [call.args[0].name for call in execute_mock.await_args_list] == [
        "first",
        "second",
    ]
    assert len(ledger.attempted_signatures) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("next_questions", "expected_question"),
    [
        (("순위를 확인한다",), "순위를 확인한다"),
        ((), "점수를 확인한다"),
    ],
)
async def test_partial_read_only_terminal_advances_latest_question_and_gap(
    next_questions: tuple[str, ...],
    expected_question: str,
) -> None:
    calls: list[tuple[str, str]] = []

    async def execute(
        asset: AssetRef,
        question: str,
        _ledger: ResolutionLedger,
    ) -> AssetResult:
        calls.append((asset.name, question))
        if asset.name == "first":
            return AssetResult(
                asset_type="skill",
                asset_name=asset.name,
                status=AssetExecutionStatus.FAILED_TERMINAL,
                resolved_claims=("score",),
                unresolved_claims=("rank",),
                next_questions=next_questions,
                evidence=(
                    {
                        "claim_id": "score",
                        "value": "70",
                        "source_url": "https://example.test/score",
                        "fresh": True,
                    },
                ),
            )
        return AssetResult(
            asset_type="skill",
            asset_name=asset.name,
            status=AssetExecutionStatus.COMPLETED,
            resolved_claims=("rank",),
            evidence=(
                {
                    "claim_id": "rank",
                    "value": "5",
                    "source_url": "https://example.test/rank",
                    "fresh": True,
                },
            ),
        )

    transition = ProblemTransition(
        original_goal="점수와 순위를 알려준다",
        previous_question="점수와 순위?",
        triggering_observation="failed_terminal",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="점수를 확인한다",
        required_claims=("score", "rank"),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="read_only_terminal_fallback",
    )
    ledger = ResolutionLedger()

    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(
            AssetRef("skill", "first"),
            AssetRef("skill", "second"),
        ),
        budget=ResolutionBudget(max_steps=3, max_tool_calls=3),
        ledger=ledger,
    )

    assert outcome.stop_reason == "resolved"
    assert outcome.goal.original_goal == transition.original_goal
    assert outcome.goal.status is GoalStatus.RESOLVED
    assert outcome.goal.resolved_claims == ("rank", "score")
    assert calls == [("first", "점수를 확인한다"), ("second", expected_question)]
    assert attempt_signature(
        question=expected_question,
        asset_type="skill",
        asset_name="second",
        parameters={"selected_gap": "rank"},
    ) in ledger.attempted_signatures
    assert attempt_signature(
        question=expected_question,
        asset_type="skill",
        asset_name="second",
        parameters={"selected_gap": "score"},
    ) not in ledger.attempted_signatures


@pytest.mark.asyncio
async def test_existing_ledger_evidence_resolves_before_new_retrieval() -> None:
    ledger = ResolutionLedger()
    prior = AssetResult(
        asset_type="recipe",
        asset_name="sports-live",
        status=AssetExecutionStatus.FAILED_TERMINAL,
        evidence=(
            {
                "claim_id": "score",
                "value": "70",
                "source_url": "https://example.test/score",
                "fresh": True,
            },
        ),
    )
    ledger.append_asset_result(prior)
    execute = AsyncMock(side_effect=AssertionError("retrieval must not run"))
    transition = ProblemTransition(
        original_goal="현재 경기 결과를 알려준다",
        previous_question="현재 스코어는?",
        triggering_observation="failed_terminal",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="현재 스코어를 확인한다",
        required_claims=("score",),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="read_only_terminal_fallback",
    )

    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(AssetRef("skill", "fallback"),),
        budget=ResolutionBudget(max_steps=3),
        ledger=ledger,
    )

    assert outcome.stop_reason == "resolved_from_existing_evidence"
    assert outcome.goal.status is GoalStatus.RESOLVED
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_previously_attempted_signature_is_not_executed_again() -> None:
    transition = ProblemTransition(
        original_goal="현재 경기 결과를 알려준다",
        previous_question="현재 스코어는?",
        triggering_observation="failed_terminal",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="현재 스코어를 확인한다",
        required_claims=("score",),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="read_only_terminal_fallback",
    )
    asset = AssetRef("skill", "fallback")
    ledger = ResolutionLedger()
    ledger.record_attempt(
        attempt_signature(
            question=transition.next_question,
            asset_type=asset.asset_type,
            asset_name=asset.name,
            parameters={"selected_gap": "score"},
        )
    )
    execute = AsyncMock(side_effect=AssertionError("duplicate must not run"))

    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(asset,),
        budget=ResolutionBudget(max_steps=3),
        ledger=ledger,
    )

    assert outcome.stop_reason == "repeated_attempt_signature"
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_exhausted_budget_does_not_start_supporting_asset() -> None:
    transition = ProblemTransition(
        original_goal="현재 경기 결과를 알려준다",
        previous_question="현재 스코어는?",
        triggering_observation="failed_terminal",
        goal_status=GoalStatus.UNRESOLVED,
        unresolved_gap="score",
        next_question="현재 스코어를 확인한다",
        required_claims=("score",),
        recommended_mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
        transition_reason="read_only_terminal_fallback",
    )
    execute = AsyncMock(side_effect=AssertionError("budget must stop execution"))

    outcome = await EvidenceInvestigationController(
        execute_supporting_asset=execute
    ).run(
        transition,
        supporting_assets=(AssetRef("skill", "fallback"),),
        budget=ResolutionBudget(max_steps=0),
        ledger=ResolutionLedger(),
    )

    assert outcome.stop_reason == "budget_exhausted"
    execute.assert_not_awaited()
