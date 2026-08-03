from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.resolution_controller import ResolutionController
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    GoalStatus,
    ResolutionBudget,
)
from simpleclaw.agent.turn_planner import plan_turn_with_llm
from simpleclaw.llm.models import LLMResponse

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed_tools", [[], ["execute_skill"]])
async def test_lpga_exact_asset_never_calls_generic_collector(
    allowed_tools: list[str],
) -> None:
    catalog = PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type="recipe",
                name="sports-live",
                description="Naver Sports structured live results",
                domains=("sports",),
                intents=("current_result",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=True,
                direct_answer=True,
                requires_confirmation=False,
                output_contract="asset_result.v1",
                declared=True,
                runtime_visible=True,
                coverage="full_coverage",
                input_contract="query.v1",
                fallback_modes=("answer_with_evidence",),
            ),
        ),
        fingerprint="sports-live-catalog",
    )
    planner_payload = {
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": "어제 유해란 LPGA 성적과 순위",
            "unresolved_references": [],
            "ignored_context_reason": "",
        },
        "clarification": {
            "required": False,
            "question": "",
            "options": [],
            "reason": "",
        },
        "domains": ["sports"],
        "intents": ["current_result"],
        "fact_check": {
            "required": True,
            "owner": "asset",
            "domain": "sports",
            "intents": ["current_result"],
            "entities": [
                {"kind": "athlete", "value": "유해란"},
                {"kind": "league", "value": "LPGA"},
            ],
            "reference_date": "2026-08-02",
            "search_query": "",
            "required_claims": ["score", "rank"],
            "freshness_required": True,
            "reason": "current result",
        },
        "capability": {
            "coverage": "full_coverage",
            "primary_asset": {
                "asset_type": "recipe",
                "asset_name": "sports-live",
            },
            "supporting_assets": [],
            "fallback_modes": ["answer_with_evidence"],
            "reason": "exact sports recipe",
        },
        "execution": {
            "mode": "answer_with_evidence",
            "allowed_tools": allowed_tools,
            "requires_confirmation": False,
            "complexity_signals": [],
            "reason": "unresolved fallback only",
        },
        "confidence": 1,
        "decision_summary": "exact sports recipe",
    }
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(planner_payload, ensure_ascii=False)
        )
    )
    candidates = ContextCandidateSet(
        candidates=(),
        total_chars=0,
        truncated=False,
    )
    plan = await plan_turn_with_llm(
        "어제 유해란 LPGA 성적과 순위",
        candidates=candidates,
        catalog=catalog,
        router=router,
    )

    gate_result = PlanGate().evaluate(
        plan,
        candidates=candidates,
        catalog=catalog,
    )

    assert gate_result.status is GateStatus.PASS
    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "sports-live"
    assert plan.capability.supporting_assets == ()
    assert plan.execution.allowed_tools == ()

    exact = AsyncMock()
    exact.execute.return_value = AssetResult(
        asset_type="recipe",
        asset_name="sports-live",
        status=AssetExecutionStatus.COMPLETED,
        resolved_claims=("score", "rank"),
        evidence=(
            {
                "claim_id": "score",
                "value": "70",
                "source_url": "https://example.test/lpga",
                "fresh": True,
            },
        ),
        data={"text": "70타, 공동 5위"},
    )
    generic_kbo = AsyncMock(side_effect=AssertionError("generic KBO collector called"))
    outcome = await ResolutionController(
        capability_executor=exact,
        answer_with_evidence=generic_kbo,
    ).resolve(plan, budget=ResolutionBudget(max_steps=3))
    assert outcome.goal.status is GoalStatus.RESOLVED
    generic_kbo.assert_not_awaited()
