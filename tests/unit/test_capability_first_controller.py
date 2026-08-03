from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.resolution_controller import ResolutionController
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    CapabilityCoverage,
    ExecutionMode,
    GoalStatus,
    ResolutionBudget,
)
from simpleclaw.agent.turn_plan import (
    AssetRef,
    CapabilityPlan,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)


def _plan(
    *,
    intents: tuple[str, ...] = ("current_result",),
    required_claims: tuple[str, ...] = ("score",),
    freshness_required: bool = False,
    supporting_assets: tuple[AssetRef, ...] = (),
    fallback_modes: tuple[ExecutionMode, ...] = (),
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="LPGA",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="LPGA 유해란 현재 스코어와 순위를 알려줘",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("sports",),
        intents=intents,
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.ASSET,
            domain="sports",
            entities=(),
            search_query="",
            intents=intents,
            required_claims=required_claims,
            freshness_required=freshness_required,
        ),
        execution=ExecutionPlan(mode=ExecutionMode.ANSWER_WITH_EVIDENCE),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("skill", "naver-sports-skill"),
            supporting_assets=supporting_assets,
            fallback_modes=fallback_modes,
        ),
        confidence=1,
        decision_summary="exact",
    )


@pytest.mark.asyncio
async def test_exact_asset_resolves_before_mode_callback() -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
        asset_type="skill",
        asset_name="naver-sports-skill",
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
        data={"text": "70타"},
    )
    evidence_handler = AsyncMock(side_effect=AssertionError("mode must not run"))
    outcome = await ResolutionController(
        capability_executor=executor,
        answer_with_evidence=evidence_handler,
    ).resolve(_plan(), budget=ResolutionBudget(max_steps=3))
    assert outcome.goal.status is GoalStatus.RESOLVED
    assert outcome.text == "70타"
    evidence_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_result_cannot_disable_freshness_at_controller_boundary() -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
        asset_type="skill",
        asset_name="naver-sports-skill",
        status=AssetExecutionStatus.COMPLETED,
        resolved_claims=("score",),
        evidence=(
            {
                "claim_id": "score",
                "value": "70",
                "source_url": "https://example.test/score",
                "fresh": None,
            },
        ),
        data={"text": "70타"},
    )

    outcome = await ResolutionController(capability_executor=executor).resolve(
        _plan(freshness_required=False),
        budget=ResolutionBudget(max_steps=3),
    )

    assert outcome.validation.allow_final is False
    assert outcome.validation.blocked_claims == ("score",)
    assert "unknown_freshness:score" in outcome.validation.limitations
    assert outcome.text != "70타"


@pytest.mark.asyncio
async def test_definition_uses_typed_non_freshness_policy() -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
        asset_type="skill",
        asset_name="dictionary-skill",
        status=AssetExecutionStatus.COMPLETED,
        resolved_claims=("definition",),
        evidence=(
            {
                "claim_id": "definition",
                "value": "a meaning",
                "source_url": "https://example.test/definition",
                "fresh": None,
            },
        ),
        data={"text": "정의"},
    )

    outcome = await ResolutionController(capability_executor=executor).resolve(
        _plan(intents=("definition",), required_claims=("definition",)),
        budget=ResolutionBudget(max_steps=3),
    )

    assert outcome.validation.allow_final is True
    assert outcome.text == "정의"


@pytest.mark.asyncio
async def test_exact_terminal_uses_allowlisted_evidence_fallback_once() -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
        asset_type="skill",
        asset_name="naver-sports-skill",
        status=AssetExecutionStatus.FAILED_TERMINAL,
        unresolved_claims=("score",),
    )
    evidence_handler = AsyncMock(
        return_value=AssetResult(
            asset_type="skill",
            asset_name="sports-fallback",
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
            data={"text": "70타"},
        )
    )
    plan = _plan(
        supporting_assets=(AssetRef("skill", "sports-fallback"),),
        fallback_modes=(ExecutionMode.ANSWER_WITH_EVIDENCE,),
    )

    outcome = await ResolutionController(
        capability_executor=executor,
        answer_with_evidence=evidence_handler,
    ).resolve(plan, budget=ResolutionBudget(max_steps=3, max_tool_calls=3))

    assert outcome.goal.status is GoalStatus.RESOLVED
    assert outcome.text == "70타"
    assert outcome.transition is not None
    assert outcome.transition.original_goal == plan.context.standalone_question
    assert outcome.transition.required_claims == ("score",)
    assert outcome.transition.recommended_mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    evidence_handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_exact_terminal_reuses_nested_evidence_before_fallback() -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
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
        data={"text": "확인된 nested helper 결과: 70타"},
    )
    evidence_handler = AsyncMock(side_effect=AssertionError("fallback must not run"))

    outcome = await ResolutionController(
        capability_executor=executor,
        answer_with_evidence=evidence_handler,
    ).resolve(
        _plan(
            supporting_assets=(AssetRef("skill", "sports-fallback"),),
            fallback_modes=(ExecutionMode.ANSWER_WITH_EVIDENCE,),
        ),
        budget=ResolutionBudget(max_steps=3),
    )

    assert outcome.goal.status is GoalStatus.RESOLVED
    assert outcome.stop_reason == "exact_asset_resolved"
    assert outcome.text == "확인된 nested helper 결과: 70타"
    evidence_handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_terminal_without_fallback_scope_returns_limited_response() -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
        asset_type="recipe",
        asset_name="sports-live",
        status=AssetExecutionStatus.FAILED_TERMINAL,
        unresolved_claims=("score",),
    )
    evidence_handler = AsyncMock(side_effect=AssertionError("fallback must not run"))

    outcome = await ResolutionController(
        capability_executor=executor,
        answer_with_evidence=evidence_handler,
    ).resolve(_plan(), budget=ResolutionBudget(max_steps=3))

    assert outcome.goal.status is GoalStatus.UNRESOLVED
    assert outcome.transition is None
    assert outcome.stop_reason == "exact_asset_limited"
    assert "추가 확인 필요: score" in outcome.text
    assert outcome.mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    evidence_handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "side_effect"),
    [
        (AssetExecutionStatus.DENIED, False),
        (AssetExecutionStatus.PARTIAL_SUCCESS, True),
        (AssetExecutionStatus.UNKNOWN_EFFECT, True),
    ],
)
async def test_blocked_exact_result_never_falls_back(
    status: AssetExecutionStatus,
    side_effect: bool,
) -> None:
    executor = AsyncMock()
    executor.execute.return_value = AssetResult(
        asset_type="skill",
        asset_name="action",
        status=status,
        side_effect=side_effect,
    )
    evidence_handler = AsyncMock(side_effect=AssertionError("fallback must not run"))

    outcome = await ResolutionController(
        capability_executor=executor,
        answer_with_evidence=evidence_handler,
    ).resolve(
        _plan(fallback_modes=(ExecutionMode.ANSWER_WITH_EVIDENCE,)),
        budget=ResolutionBudget(max_steps=3),
    )

    assert outcome.goal.status is GoalStatus.BLOCKED
    assert outcome.stop_reason == "exact_asset_blocked"
    evidence_handler.assert_not_awaited()
