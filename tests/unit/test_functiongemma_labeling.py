"""BIZ-512 weak-label budget·queue·no-provider-default 계약."""

from __future__ import annotations

import json

import pytest

from scripts.dev.run_functiongemma_intent_poc import (
    _bounded_catalog,
    _provider_prompt_diagnostic,
)
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
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
from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CandidateAsset,
)
from simpleclaw.evaluation.functiongemma_dataset import (
    SanitizedCase,
    SanitizedMessage,
)
from simpleclaw.evaluation.functiongemma_labeling import (
    LabelingBudget,
    candidate_fingerprint,
    label_cases,
    labeling_public_summary,
)


def _case(number: int) -> SanitizedCase:
    return SanitizedCase(
        f"case:{number}", f"group:{number}", (), f"private-{number}",
        "telegram", f"fp:{number}", "train",
    )


def _asset(name: str = "search") -> PlannerAsset:
    return PlannerAsset(
        asset_type="skill",
        name=name,
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


def _plan(
    confidence: float = 0.9,
    asset_name: str = "search",
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="private",
        context=ContextSelection(ContextRelation.STANDALONE, False, (), "q"),
        clarification=ClarificationPlan(False),
        domains=("news",),
        intents=("lookup",),
        fact_check=FactCheckPlan(False, EvidenceOwner.NONE, "", (), ""),
        execution=ExecutionPlan(
            ExecutionMode.EXECUTE_ASSET,
            AssetRef("skill", asset_name),
            (AssetRef("skill", asset_name),),
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
        "budget.exhausted",
    }
    summary = labeling_public_summary(result)
    assert "private-1" not in str(summary)


@pytest.mark.asyncio
async def test_out_of_set_target_is_not_promoted_after_planner_call() -> None:
    exposed: tuple[str, ...] = ()

    async def planner(case, candidates):
        nonlocal exposed
        exposed = tuple(candidate.asset_id for candidate in candidates)
        return _plan(asset_name="outside")

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
    )

    assert exposed == ("skill:search", "__none__")
    assert result.labeled == ()
    assert result.adjudication_queue[0].reason_codes == (
        "boundary.unknown_asset",
    )


@pytest.mark.asyncio
async def test_pre_call_candidates_and_fingerprint_remain_identical() -> None:
    captured: tuple[str, ...] = ()

    async def planner(case, candidates):
        nonlocal captured
        captured = tuple(candidate.asset_id for candidate in candidates)
        return _plan()

    result = await label_cases(
        [_case(1)],
        catalog_assets=[_asset()],
        planner=planner,
        allow_provider_calls=True,
    )

    assert len(result.labeled) == 1
    labeled = result.labeled[0]
    assert tuple(candidate.asset_id for candidate in labeled.candidates) == captured
    assert labeled.candidate_fingerprint == candidate_fingerprint(
        labeled.candidates
    )


def test_bounded_catalog_and_actual_prompt_match_case_candidates() -> None:
    assets = (_asset("search"), _asset("weather"))
    catalog = PlannerCatalog(assets=assets, fingerprint="full")
    candidates = (
        CandidateAsset("skill:weather", "skill", "weather"),
        CandidateAsset("skill:search", "skill", "search"),
        CandidateAsset(NO_ASSET, "none", NO_ASSET),
    )
    case = SanitizedCase(
        "opaque-case-a",
        "source:fp",
        (
            SanitizedMessage(
                "opaque-turn-a",
                "user",
                "앞 질문",
            ),
        ),
        "현재 질문",
        "telegram",
        "fp",
    )

    bounded = _bounded_catalog(catalog, candidates)
    prompt = _provider_prompt_diagnostic(case, candidates, catalog)
    payload = json.loads(prompt)

    assert bounded.fingerprint == candidate_fingerprint(candidates)
    assert [
        f"{item['type']}:{item['name']}"
        for item in payload["capability_catalog"]
    ] == ["skill:weather", "skill:search"]
    assert payload["catalog_fingerprint"] == candidate_fingerprint(candidates)
    assert "101" not in prompt
    assert "102" not in prompt


def test_provider_prompt_diagnostic_rejects_raw_message_id() -> None:
    asset = _asset()
    catalog = PlannerCatalog(assets=(asset,), fingerprint="full")
    candidates = (
        CandidateAsset("skill:search", "skill", "search"),
        CandidateAsset(NO_ASSET, "none", NO_ASSET),
    )
    case = SanitizedCase(
        "opaque-case-a",
        "source:fp",
        (SanitizedMessage("msg:101", "user", "앞 질문"),),
        "현재 질문",
        "telegram",
        "fp",
    )

    with pytest.raises(ValueError, match="identifier_leak"):
        _provider_prompt_diagnostic(case, candidates, catalog)
