"""BIZ-512 weak-label budget·queue·no-provider-default 계약."""

from __future__ import annotations

import asyncio

import pytest

from simpleclaw.agent.planner_catalog import PlannerAsset
from simpleclaw.agent.turn_plan import (
    AssetRef,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.evaluation.functiongemma_dataset import SanitizedCase
from simpleclaw.evaluation.functiongemma_labeling import (
    LabelingBudget,
    PlannerResponse,
    label_cases,
    labeling_public_summary,
)


def _case(number: int) -> SanitizedCase:
    return SanitizedCase(
        f"case:{number}", f"group:{number}", (), f"private-{number}",
        "telegram", f"fp:{number}", "train",
    )


def _asset() -> PlannerAsset:
    return PlannerAsset(
        asset_type="skill",
        name="search",
        description="search",
        domains=("news",),
        intents=("lookup",),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
        direct_answer=True,
        requires_confirmation=False,
        output_contract=None,
        declared=True,
        runtime_visible=True,
    )


def _plan(confidence: float = 0.9) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="private",
        context=ContextSelection(ContextRelation.STANDALONE, False, (), "q"),
        clarification=ClarificationPlan(False),
        domains=("news",),
        intents=("lookup",),
        fact_check=FactCheckPlan(False, EvidenceOwner.NONE, "", (), ""),
        execution=ExecutionPlan(
            ExecutionMode.EXECUTE_ASSET,
            AssetRef("skill", "search"),
            (AssetRef("skill", "search"),),
            ("execute_skill",),
            False,
            "",
        ),
        confidence=confidence,
        decision_summary="",
    )


@pytest.mark.asyncio
async def test_provider_requires_explicit_opt_in() -> None:
    async def planner(case, candidates):
        raise AssertionError("must not call")

    with pytest.raises(PermissionError):
        await label_cases(
            [_case(1)],
            catalog_assets=[_asset()],
            planner=planner,
            allow_provider_calls=False,
        )


@pytest.mark.asyncio
async def test_budget_stops_calls_and_low_confidence_goes_to_queue() -> None:
    calls = 0

    async def planner(case, candidates):
        nonlocal calls
        calls += 1
        return _plan(0.4 if case.case_id.endswith("1") else 0.9)

    result = await label_cases(
        [_case(1), _case(2), _case(3)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_calls=2),
    )
    assert calls == result.provider_calls == 2
    assert len(result.labeled) == 1
    assert {reason for item in result.adjudication_queue for reason in item.reason_codes} == {
        "confidence.low",
        "budget.call_exhausted",
    }
    summary = labeling_public_summary(result)
    assert "private-1" not in str(summary)


@pytest.mark.asyncio
async def test_in_flight_timeout_cancels_and_rejects_label() -> None:
    cancelled = False

    async def planner(case, candidates):
        nonlocal cancelled
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return _plan()

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_seconds=0.01),
    )
    assert cancelled
    assert result.labeled == ()
    assert result.adjudication_queue[0].reason_codes == (
        "budget.time_exhausted",
    )


@pytest.mark.asyncio
async def test_post_call_deadline_result_is_rejected() -> None:
    times = iter((0.0, 0.0, 0.0, 0.02, 0.02))

    async def planner(case, candidates):
        return _plan()

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_seconds=0.01),
        clock=lambda: next(times),
    )
    assert result.labeled == ()
    assert result.adjudication_queue[0].reason_codes == (
        "budget.time_exhausted",
    )


@pytest.mark.asyncio
async def test_token_cap_rejects_overshoot_and_stops_next_call() -> None:
    calls = 0

    async def planner(case, candidates):
        nonlocal calls
        calls += 1
        return PlannerResponse(_plan(), token_count=6)

    result = await label_cases(
        [_case(1), _case(2)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_tokens=5),
    )
    assert calls == 1
    assert result.labeled == ()
    assert result.provider_tokens == 6
    assert all(
        item.reason_codes == ("budget.token_exhausted",)
        for item in result.adjudication_queue
    )


@pytest.mark.asyncio
async def test_unknown_token_usage_fails_closed_when_cap_enabled() -> None:
    async def planner(case, candidates):
        return _plan()

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
        budget=LabelingBudget(max_tokens=10),
    )
    assert result.labeled == ()
    assert not result.token_usage_supported
    assert result.adjudication_queue[0].reason_codes == (
        "budget.token_usage_unavailable",
    )
