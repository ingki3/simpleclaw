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


def _plan() -> UnifiedTurnPlan:
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
        intents=("current_result",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.ASSET,
            domain="sports",
            entities=(),
            search_query="",
            required_claims=("score",),
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
