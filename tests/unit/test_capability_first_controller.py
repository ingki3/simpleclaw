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
