from __future__ import annotations

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

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_lpga_exact_asset_never_calls_generic_collector() -> None:
    exact = AsyncMock()
    exact.execute.return_value = AssetResult(
        asset_type="skill",
        asset_name="naver-sports-skill",
        status=AssetExecutionStatus.COMPLETED,
        resolved_claims=("score",),
        evidence=(
            {
                "claim_id": "score",
                "value": "70",
                "source_url": "https://example.test/lpga",
                "fresh": True,
            },
        ),
        data={"text": "70타"},
    )
    generic_kbo = AsyncMock(side_effect=AssertionError("generic KBO collector called"))
    plan = UnifiedTurnPlan(
        original_text="LPGA 유해란 스코어",
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
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("skill", "naver-sports-skill"),
        ),
        execution=ExecutionPlan(mode=ExecutionMode.ANSWER_WITH_EVIDENCE),
        confidence=1,
        decision_summary="exact",
    )
    outcome = await ResolutionController(
        capability_executor=exact,
        answer_with_evidence=generic_kbo,
    ).resolve(plan, budget=ResolutionBudget(max_steps=3))
    assert outcome.goal.status is GoalStatus.RESOLVED
    generic_kbo.assert_not_awaited()

