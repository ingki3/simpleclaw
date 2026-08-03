from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.capability_executor import CapabilityExecutor
from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.evidence_investigation import EvidenceInvestigationController
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.resolution_controller import ResolutionController
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    CapabilityCoverage,
    ExecutionMode,
    GoalStatus,
    ProblemTransition,
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
from simpleclaw.agent.turn_planner import plan_turn_with_llm
from simpleclaw.llm.models import LLMResponse

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_mode", ["direct_answer", "answer_with_evidence"])
async def test_kbo_completed_result_asset_zero_plan_repairs_to_exact_recipe(
    execution_mode: str,
) -> None:
    """production-like asset-0 planner output은 typed catalog로만 exact 보정한다."""
    catalog = PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type="recipe",
                name="sports-live",
                description="Naver structured sports live and completed results",
                domains=("sports",),
                intents=("current_result", "completed_result"),
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
        fingerprint="sports-results-catalog",
    )
    planner_payload = {
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": "어제 프로야구 경기 결과 알려줘",
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
        "intents": ["completed_result"],
        "fact_check": {
            "required": True,
            "owner": "planner",
            "domain": "sports",
            "intents": ["completed_result"],
            "entities": [{"kind": "league", "value": "KBO"}],
            "reference_date": "2026-08-02",
            "search_query": "2026-08-02 KBO 경기 결과",
            "required_claims": ["score", "winner"],
            "freshness_required": True,
            "reason": "completed result needs evidence",
        },
        "capability": {
            "coverage": "no_match",
            "primary_asset": {
                "asset_type": "none",
                "asset_name": "__none__",
            },
            "supporting_assets": [],
            "fallback_modes": ["answer_with_evidence"],
            "reason": "planner omitted asset",
        },
        "execution": {
            "mode": execution_mode,
            "allowed_tools": [],
            "requires_confirmation": False,
            "complexity_signals": [],
            "reason": "evidence required",
        },
        "confidence": 0.8,
        "decision_summary": "completed KBO result",
    }
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(planner_payload, ensure_ascii=False))
    )
    candidates = ContextCandidateSet(candidates=(), total_chars=0, truncated=False)

    plan = await plan_turn_with_llm(
        "어제 프로야구 경기 결과 알려줘",
        candidates=candidates,
        catalog=catalog,
        router=router,
    )
    gate = PlanGate().evaluate(plan, candidates=candidates, catalog=catalog)

    assert plan.capability.primary_asset is None
    assert plan.execution.allowed_tools == ()
    assert gate.status is GateStatus.PASS
    assert gate.effective_plan is not None
    assert gate.effective_plan.capability.primary_asset == AssetRef(
        "recipe", "sports-live"
    )
    assert gate.effective_plan.fact_check.owner is EvidenceOwner.ASSET


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


@pytest.mark.asyncio
async def test_sports_exact_terminal_uses_one_bounded_allowlisted_fallback() -> None:
    catalog = PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type="recipe",
                name="sports-live",
                description="Structured live sports result",
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
            PlannerAsset(
                asset_type="skill",
                name="sports-secondary",
                description="Secondary structured sports evidence",
                domains=("sports",),
                intents=("current_result",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=True,
                direct_answer=False,
                requires_confirmation=False,
                output_contract="asset_result.v1",
                declared=True,
                runtime_visible=True,
                input_contract="query.v1",
            ),
        ),
        fingerprint="terminal-fallback-catalog",
    )
    plan = UnifiedTurnPlan(
        original_text="어제 유해란 LPGA 성적",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="어제 유해란 LPGA 성적",
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
            intents=("current_result",),
            reference_date="2026-08-02",
            required_claims=("score",),
            freshness_required=True,
        ),
        execution=ExecutionPlan(mode=ExecutionMode.ANSWER_WITH_EVIDENCE),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", "sports-live"),
            supporting_assets=(AssetRef("skill", "sports-secondary"),),
            fallback_modes=(ExecutionMode.ANSWER_WITH_EVIDENCE,),
        ),
        confidence=1,
        decision_summary="exact then bounded fallback",
        catalog_fingerprint=catalog.fingerprint,
    )
    execute_recipe = AsyncMock(
        return_value={
            "schema": "asset_result.v1",
            "status": "failed_terminal",
            "unresolved_claims": ["score"],
        }
    )
    execute_supporting = AsyncMock(
        return_value=AssetResult(
            asset_type="skill",
            asset_name="sports-secondary",
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
    )

    async def evidence_mode(
        _plan: UnifiedTurnPlan,
        transition: ProblemTransition | None,
        ledger: ResolutionLedger,
        budget: ResolutionBudget,
    ) -> AssetResult:
        assert transition is not None
        investigated = await EvidenceInvestigationController(
            execute_supporting_asset=execute_supporting
        ).run(
            transition,
            supporting_assets=plan.capability.supporting_assets,
            budget=budget,
            ledger=ledger,
        )
        assert investigated.last_result is not None
        return investigated.last_result

    outcome = await ResolutionController(
        capability_executor=CapabilityExecutor(
            catalog=catalog,
            execute_recipe=execute_recipe,
        ),
        answer_with_evidence=evidence_mode,
    ).resolve(
        plan,
        budget=ResolutionBudget(max_steps=3, max_tool_calls=3),
    )

    assert outcome.goal.status is GoalStatus.RESOLVED
    assert outcome.text == "70타"
    assert outcome.transition is not None
    assert outcome.transition.original_goal == plan.context.standalone_question
    assert outcome.transition.required_claims == ("score",)
    execute_recipe.assert_awaited_once()
    execute_supporting.assert_awaited_once()
