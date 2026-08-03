from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.capability_executor import CapabilityExecutor
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.evidence_investigation import EvidenceInvestigationController
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import (
    PlannerAsset,
    PlannerCatalog,
    build_planner_catalog,
)
from simpleclaw.agent.resolution_controller import ResolutionController
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    CapabilityCoverage,
    ExecutionMode,
    GoalResolutionState,
    GoalStatus,
    ProblemTransition,
    ResolutionBudget,
)
from simpleclaw.agent.result_validator import CommonResultValidator
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
from simpleclaw.capability import CapabilityMetadata
from simpleclaw.llm.models import LLMResponse, ToolCall
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.skills.models import SkillDefinition

pytestmark = pytest.mark.offline

SPORTS_RECIPE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "recipes"
    / "sports-live"
    / "recipe.yaml"
)


def _orchestrator_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-2.0-flash
      api_key: test-key
agent:
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 2
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: AGENT.md
      type: agent
memory:
  rag:
    enabled: false
""",
        encoding="utf-8",
    )
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent", encoding="utf-8")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_only", "side_effects", "requires_confirmation"),
    [
        (False, False, False),
        (True, True, False),
        (True, False, True),
    ],
    ids=("write", "side-effect", "confirmation"),
)
async def test_exact_executor_never_runs_non_read_only_evidence_asset(
    read_only: bool,
    side_effects: bool,
    requires_confirmation: bool,
) -> None:
    asset = PlannerAsset(
        asset_type="skill",
        name="unsafe-helper",
        description="test helper",
        domains=("sports",),
        intents=("current_result",),
        read_only=read_only,
        side_effects=side_effects,
        freshness_sensitive=True,
        direct_answer=True,
        requires_confirmation=requires_confirmation,
        output_contract="asset_result.v1",
        declared=True,
        runtime_visible=True,
        coverage="full_coverage",
        input_contract="query.v1",
    )
    catalog = PlannerCatalog(assets=(asset,), fingerprint="trusted-catalog")
    plan = UnifiedTurnPlan(
        original_text="결과 조회",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="결과 조회",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("sports",),
        intents=("current_result",),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="sports",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(mode=ExecutionMode.DIRECT_ANSWER),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("skill", "unsafe-helper"),
        ),
        confidence=1,
        decision_summary="test",
        catalog_fingerprint=catalog.fingerprint,
    )
    execute_skill = AsyncMock(
        return_value={
            "schema": "asset_result.v1",
            "status": "completed",
            "data": {"text": "must not resolve"},
            "resolved_claims": [],
        }
    )

    result = await CapabilityExecutor(
        catalog=catalog,
        execute_skill=execute_skill,
    ).execute(
        plan,
        budget=ResolutionBudget(max_steps=1, max_tool_calls=1),
        ledger=ResolutionLedger(),
    )

    assert result.status is AssetExecutionStatus.UNSUPPORTED
    assert result.limitations == ("asset_not_fast_path_eligible",)
    execute_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_planner_to_validator_connected_exact_recipe_uses_one_safe_helper(
    tmp_path: Path,
) -> None:
    """Required claims는 planner output에서 production execution 경계로만 흐른다."""
    helper = SkillDefinition(
        name="naver-sports-skill",
        description="registered fake structured sports helper",
        capability=CapabilityMetadata(
            domains=("sports",),
            intents=("current_result",),
            read_only=True,
            side_effects=False,
            freshness_sensitive=True,
            direct_answer=True,
            requires_confirmation=False,
            output_contract="asset_result.v1",
            coverage="full_coverage",
            input_contract="query.v1",
            declared=True,
        ),
    )
    recipe = load_recipe(SPORTS_RECIPE)
    catalog = build_planner_catalog(
        skills=(helper,),
        recipes=(recipe,),
        native_specs=(),
    )
    planner_payload = {
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": "어제 프로야구 경기 결과 및 스코어",
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
            "entities": [],
            "reference_date": "2026-08-02",
            "search_query": "",
            "required_claims": ["game_result", "score"],
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
            "reason": "exact recipe",
        },
        "execution": {
            "mode": "answer_with_evidence",
            "allowed_tools": [],
            "requires_confirmation": False,
            "complexity_signals": [],
            "reason": "exact recipe",
        },
        "confidence": 1,
        "decision_summary": "exact recipe",
    }
    planner_router = AsyncMock()
    planner_router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(planner_payload, ensure_ascii=False))
    )
    candidates = ContextCandidateSet(candidates=(), total_chars=0, truncated=False)
    plan = await plan_turn_with_llm(
        "어제 프로야구 경기 결과 및 스코어",
        candidates=candidates,
        catalog=catalog,
        router=planner_router,
    )
    gated = PlanGate().evaluate(plan, candidates=candidates, catalog=catalog)
    assert gated.status is GateStatus.PASS

    orchestrator = AgentOrchestrator(_orchestrator_config(tmp_path))
    orchestrator._skills = [helper]
    orchestrator._skills_by_name = {helper.name: helper}
    orchestrator._skills_prompt = orchestrator._format_skills_for_prompt([helper])
    orchestrator._recipes = [recipe]
    orchestrator._router.send = AsyncMock(
        return_value=LLMResponse(
            text="",
            model="test",
            tool_calls=[
                ToolCall(
                    id="fake-helper-1",
                    name="execute_skill",
                    arguments={"skill_name": helper.name, "command": "--json"},
                )
            ],
        )
    )
    observation = {
        "ok": True,
        "items": [{"game_result": "한화 승", "score": {"away": 3, "home": 5}}],
        "claim_map": {
            claim: {
                "records": [
                    {
                        "value": value,
                        "source_url": "https://example.test/structured-result",
                        "provenance": "registered fake structured helper",
                        "observed_at": "2026-08-03T17:00:00+09:00",
                        "fresh": True,
                    }
                ]
            }
            for claim, value in (
                ("game_result", "한화 승"),
                ("score", {"away": 3, "home": 5}),
            )
        },
    }
    orchestrator._dispatch_external_skill = AsyncMock(
        return_value=json.dumps(observation, ensure_ascii=False)
    )

    outcome = await ResolutionController(
        capability_executor=CapabilityExecutor(
            catalog=catalog,
            execute_recipe=orchestrator._execute_exact_recipe_asset,
        ),
    ).resolve(
        gated.effective_plan,
        budget=ResolutionBudget(max_steps=2, max_tool_calls=1),
    )

    assert outcome.goal.status is GoalStatus.RESOLVED
    assert outcome.validation.allow_final is True
    assert outcome.asset_result is not None
    assert outcome.asset_result.status is AssetExecutionStatus.COMPLETED
    assert outcome.asset_result.resolved_claims == ("game_result", "score")
    assert orchestrator._router.send.await_count == 1
    orchestrator._dispatch_external_skill.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "execution_mode",
        "planner_intents",
        "planner_claims",
        "expected_claims",
        "planner_selected_asset",
    ),
    [
        (
            "direct_answer",
            ["completed_result"],
            ["score", "winner"],
            ("score", "winner"),
            False,
        ),
        (
            "answer_with_evidence",
            ["completed_result"],
            ["각 경기의 최종 점수와 승리 팀"],
            ("score", "winner"),
            False,
        ),
        (
            "answer_with_evidence",
            ["current_result"],
            ["2026년 8월 2일 KBO 프로야구 경기 결과 및 스코어"],
            ("game_result", "score"),
            True,
        ),
        (
            "answer_with_evidence",
            ["current_result"],
            ["어제(2026-08-02) KBO 프로야구 경기 결과 및 스코어"],
            ("game_result", "score"),
            True,
        ),
        (
            "direct_answer",
            ["current_result"],
            ["2026년 8월 2일 KBO 프로야구 경기 결과"],
            ("game_result",),
            True,
        ),
        (
            "answer_with_evidence",
            ["completed_result"],
            ["각 경기의 최종 점수", "관중 수"],
            ("score", "관중 수"),
            False,
        ),
        (
            "answer_with_evidence",
            ["completed_result"],
            ["각 경기의 최종 점수와 관중 수"],
            ("각 경기의 최종 점수와 관중 수",),
            True,
        ),
        (
            "direct_answer",
            ["current_result"],
            ["경기 결과와 관중 수"],
            ("경기 결과와 관중 수",),
            True,
        ),
        (
            "direct_answer",
            ["current_result"],
            ["어제(2026-08-02) KBO 프로야구 경기 결과 및 스코어와 관중 수"],
            ("어제(2026-08-02) KBO 프로야구 경기 결과 및 스코어와 관중 수",),
            True,
        ),
        (
            "answer_with_evidence",
            ["completed_result"],
            ["점수와 부상 선수 명단"],
            ("점수와 부상 선수 명단",),
            False,
        ),
        (
            "direct_answer",
            ["current_result"],
            ["경기 결과 및 점수"],
            ("game_result", "score"),
            True,
        ),
    ],
)
async def test_kbo_completed_result_asset_zero_plan_repairs_to_exact_recipe(
    execution_mode: str,
    planner_intents: list[str],
    planner_claims: list[str],
    expected_claims: tuple[str, ...],
    planner_selected_asset: bool,
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
        "intents": planner_intents,
        "fact_check": {
            "required": True,
            "owner": "asset" if planner_selected_asset else "planner",
            "domain": "sports",
            "intents": planner_intents,
            "entities": [{"kind": "league", "value": "KBO"}],
            "reference_date": "2026-08-02",
            "search_query": "2026-08-02 KBO 경기 결과",
            "required_claims": planner_claims,
            "freshness_required": True,
            "reason": "completed result needs evidence",
        },
        "capability": {
            "coverage": "full_coverage" if planner_selected_asset else "no_match",
            "primary_asset": {
                "asset_type": "recipe" if planner_selected_asset else "none",
                "asset_name": (
                    "sports-live" if planner_selected_asset else "__none__"
                ),
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

    if planner_selected_asset:
        assert plan.capability.primary_asset == AssetRef("recipe", "sports-live")
    else:
        assert plan.capability.primary_asset is None
    assert plan.execution.allowed_tools == ()
    assert gate.status is GateStatus.PASS
    assert gate.effective_plan is not None
    assert gate.effective_plan.capability.primary_asset == AssetRef(
        "recipe", "sports-live"
    )
    assert gate.effective_plan.fact_check.owner is EvidenceOwner.ASSET
    assert gate.effective_plan.fact_check.required_claims == expected_claims
    if any(term in planner_claims[0] for term in ("관중", "부상")):
        supported_claim = "score" if "점수" in planner_claims[0] else "game_result"
        ledger = ResolutionLedger()
        ledger.append_asset_result(
            AssetResult(
                asset_type="recipe",
                asset_name="sports-live",
                status=AssetExecutionStatus.COMPLETED,
                evidence=(
                    {
                        "claim_id": supported_claim,
                        "value": "provider observation",
                        "source_url": "https://sports.naver.com/result",
                        "observed_at": "2026-08-03T20:00:00+09:00",
                        "provenance": "Naver Sports structured API",
                        "fresh": True,
                        "usable": True,
                    },
                ),
                resolved_claims=(supported_claim,),
            )
        )
        decision = CommonResultValidator().validate(
            goal=GoalResolutionState(
                original_goal="compound sports result",
                status=GoalStatus.RESOLVED,
                resolved_claims=(supported_claim,),
                unresolved_claims=(),
            ),
            ledger=ledger,
            required_claims=expected_claims,
        )
        assert decision.allow_final is False
        assert decision.blocked_claims == expected_claims


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
