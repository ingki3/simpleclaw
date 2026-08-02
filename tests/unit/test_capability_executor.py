from __future__ import annotations

import json
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.capability_executor import CapabilityExecutor
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    CapabilityCoverage,
    ExecutionMode,
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
        execution=ExecutionPlan(mode=ExecutionMode.ANSWER_WITH_EVIDENCE),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("skill", "naver-sports-skill"),
        ),
        confidence=1.0,
        decision_summary="exact",
        catalog_fingerprint="fp",
    )


def _catalog() -> PlannerCatalog:
    return PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type="skill",
                name="naver-sports-skill",
                description="sports",
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
            ),
        ),
        fingerprint="fp",
    )


@pytest.mark.asyncio
async def test_exact_skill_receives_one_query_v1_argument_without_reselection() -> None:
    execute = AsyncMock(
        return_value=json.dumps(
            {
                "schema": "asset_result.v1",
                "status": "completed",
                "resolved_claims": ["score"],
                "data": {"text": "70타"},
            }
        )
    )
    result = await CapabilityExecutor(
        catalog=_catalog(),
        execute_skill=execute,
    ).execute(
        _plan(),
        budget=ResolutionBudget(max_steps=2),
        ledger=ResolutionLedger(),
    )
    assert result.status is AssetExecutionStatus.COMPLETED
    execute.assert_awaited_once_with(
        "naver-sports-skill",
        "LPGA 유해란 현재 스코어와 순위를 알려줘",
    )


@pytest.mark.asyncio
async def test_untyped_fast_path_result_fails_closed() -> None:
    result = await CapabilityExecutor(
        catalog=_catalog(),
        execute_skill=AsyncMock(return_value="plain text"),
    ).execute(
        _plan(),
        budget=ResolutionBudget(max_steps=2),
        ledger=ResolutionLedger(),
    )
    assert result.status is AssetExecutionStatus.FAILED_TERMINAL


@pytest.mark.asyncio
async def test_exact_recipe_identity_executes_once() -> None:
    plan = _plan()
    plan = replace(
        plan,
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", "daily"),
        ),
    )
    catalog = PlannerCatalog(
        assets=(
            replace(
                _catalog().assets[0],
                asset_type="recipe",
                name="daily",
            ),
        ),
        fingerprint="fp",
    )
    execute = AsyncMock(
        return_value={
            "schema": "asset_result.v1",
            "status": "completed",
            "resolved_claims": ["score"],
        }
    )
    result = await CapabilityExecutor(
        catalog=catalog,
        execute_recipe=execute,
    ).execute(
        plan,
        budget=ResolutionBudget(max_steps=2),
        ledger=ResolutionLedger(),
    )
    assert result.status is AssetExecutionStatus.COMPLETED
    execute.assert_awaited_once_with(
        "daily",
        {"query": "LPGA 유해란 현재 스코어와 순위를 알려줘"},
    )


@pytest.mark.asyncio
async def test_exact_executor_deadline_cancels_in_flight_await() -> None:
    async def slow_result(*_args: object) -> object:
        await asyncio.sleep(0.05)
        return {
            "schema": "asset_result.v1",
            "status": "completed",
            "resolved_claims": ["score"],
        }

    ledger = ResolutionLedger()
    result = await CapabilityExecutor(
        catalog=_catalog(),
        execute_skill=slow_result,
    ).execute(
        _plan(),
        budget=ResolutionBudget.from_seconds(max_seconds=0.01, max_steps=2),
        ledger=ledger,
    )

    assert result.status is AssetExecutionStatus.FAILED_TERMINAL
    assert result.limitations == ("deadline_exhausted",)
    assert ledger.steps_used == 1
    assert ledger.tool_calls_used == 1
