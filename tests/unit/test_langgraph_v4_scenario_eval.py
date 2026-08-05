"""BIZ-578 사용자 시나리오 evaluator 단위 회귀."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from simpleclaw.evaluation import langgraph_v4_scenario_eval as scenario_eval

from simpleclaw.agent.plan_gate import GateStatus, PlanGateResult
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.resolution_types import CapabilityCoverage, ExecutionMode
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
from simpleclaw.agent.turn_planner import PlannerUnavailable
from simpleclaw.evaluation.langgraph_v4_scenario_eval import (
    ContractIssue,
    ProviderBudgetExceeded,
    ProviderCallBudget,
    ScenarioEvaluator,
    ScenarioFixtureError,
    SideEffectCounts,
    SideEffectDetected,
    SideEffectGuard,
    aggregate_results,
    assert_sanitized_report,
    classify_contract,
    classify_ingress,
    load_scenarios,
    normalize_v4_route,
    not_scored_result,
    score_plan,
)

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/langgraph_v4_user_scenarios.jsonl"


def _asset(name: str = "reader", **changes) -> PlannerAsset:
    values = {
        "asset_type": "skill",
        "name": name,
        "description": "read only test asset",
        "domains": (),
        "intents": (),
        "read_only": True,
        "side_effects": False,
        "freshness_sensitive": False,
        "direct_answer": True,
        "requires_confirmation": False,
        "output_contract": "asset_result.v1",
        "declared": True,
        "runtime_visible": True,
        "coverage": "full_coverage",
        "input_contract": "query.v1",
    }
    values.update(changes)
    return PlannerAsset(**values)


def _catalog(*assets: PlannerAsset) -> PlannerCatalog:
    return PlannerCatalog(tuple(assets), "catalog-v1")


def _plan(
    *,
    mode: ExecutionMode = ExecutionMode.DIRECT_ANSWER,
    asset: AssetRef | None = None,
    clarify: bool = False,
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="question",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="안녕",
        ),
        clarification=ClarificationPlan(
            required=clarify, question="확인할까요?" if clarify else ""
        ),
        domains=(),
        intents=(),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="none",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=asset,
            allowed_assets=() if asset is None else (asset,),
        ),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.NO_MATCH
            if asset is None
            else CapabilityCoverage.FULL,
            primary_asset=asset,
            supporting_assets=() if asset is None else (asset,),
        ),
        confidence=1,
        decision_summary="test",
        catalog_fingerprint="catalog-v1",
    )


def test_fixture_loads_strict_32_case_gold() -> None:
    cases = load_scenarios(FIXTURE)

    assert len(cases) == 32
    assert sum(case.critical for case in cases) == 9
    assert sum(bool(case.history) for case in cases) == 5
    assert (
        sum(case.expected.evaluation_scope == "runtime_scored" for case in cases) == 25
    )
    assert (
        sum(case.expected.evaluation_scope != "runtime_scored" for case in cases) == 7
    )


def test_live_recipe_slash_commands_bypass_planner_classification() -> None:
    recipe_names = ("krstock", "usstock")

    assert classify_ingress("/krstock", recipe_names) == "recipe_command"
    assert classify_ingress("/usstock market=all", recipe_names) == "recipe_command"
    assert classify_ingress("/cron list", recipe_names) == "native_command"
    assert classify_ingress("ordinary question", recipe_names) is None


def test_scope_gaps_do_not_enter_runtime_quality_denominator() -> None:
    cases = load_scenarios(FIXTURE)
    runtime = score_plan(
        cases[0],
        _plan(),
        PlanGateResult(GateStatus.PASS, _plan()),
        _catalog(),
    )
    operator = not_scored_result(cases[27])
    attachment = not_scored_result(cases[22])
    report = aggregate_results(
        [runtime, operator, attachment],
        provider_calls=1,
        provider_call_budget=64,
        provider_backend="test",
        provider_model="model",
        side_effect_counts=SideEffectCounts(),
        elapsed_seconds=1,
    )

    assert report["summary"]["total_inventory_cases"] == 3
    assert report["summary"]["scored_cases"] == 1
    assert report["summary"]["not_scored_cases"] == 2
    assert report["summary"]["pass_rate"] == 1
    assert report["cases"][1]["passed"] is None
    assert report["cases"][2]["passed"] is None
    assert report["not_scored_inventory"] == {
        "attachment_scope_gap": 1,
        "operator_scope_gap": 1,
    }


@pytest.mark.asyncio
async def test_early_stop_preserves_full_inventory_and_schedule() -> None:
    cases = load_scenarios(FIXTURE)

    async def unavailable_planner(_text, **_kwargs):
        raise ValueError("synthetic unavailable")

    report = await ScenarioEvaluator(
        catalog=_catalog(),
        router=object(),
        planner=unavailable_planner,
        ingress_recipe_names=("krstock", "usstock"),
    ).evaluate(cases, repeat_critical=3)

    assert report["decision"] == "hold"
    assert report["summary"]["total_inventory_cases"] == 32
    assert report["summary"]["total_runs"] == 46
    assert report["summary"]["scored_runs"] == 39
    assert report["summary"]["evaluated_scored_runs"] == 3
    assert report["summary"]["unevaluated_scored_runs"] == 36
    assert report["summary"]["quality_evaluation_complete"] is False
    assert report["summary"]["not_scored_cases"] == 7
    assert sum(row["evaluated"] for row in report["cases"]) == 3
    assert sum(row["passed"] is None for row in report["cases"]) == 43


@pytest.mark.asyncio
async def test_planner_unavailable_is_the_only_schema_failure_phase() -> None:
    case = load_scenarios(FIXTURE)[0]

    async def unavailable_planner(_text, **_kwargs):
        raise PlannerUnavailable("raw provider response")

    report = await ScenarioEvaluator(
        catalog=_catalog(), router=object(), planner=unavailable_planner
    ).evaluate((case,), repeat_critical=1)

    row = report["cases"][0]
    assert report["summary"]["schema_validity_rate"] == 0
    assert row["failure_phase"] == "planner_schema"
    assert row["error_codes"] == ["planner.schema_or_unavailable"]
    assert "raw provider response" not in json.dumps(report)


@pytest.mark.asyncio
async def test_connected_value_error_preserves_planner_schema_usage_and_schedule() -> (
    None
):
    case = load_scenarios(FIXTURE)[4]
    asset = _asset()
    plan = _plan(asset=AssetRef("skill", asset.name))
    calls = 0

    class UsageResponse:
        backend_name = "fixture"
        model = "fixture-model"
        usage = {"input_tokens": 7, "output_tokens": 3}

    class UsageRouter:
        async def send(self, _request):
            return UsageResponse()

    async def planner(_text, *, router, **_kwargs):
        await router.send(object())
        return plan

    async def connected_executor(_case, _plan, _assets):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("credential=must-not-appear")
        return "completed", SideEffectCounts()

    report = await ScenarioEvaluator(
        catalog=_catalog(asset),
        router=UsageRouter(),
        planner=planner,
        execute_read_only_contract_assets=True,
        connected_executor=connected_executor,
        connected_executor_kind="fixture",
    ).evaluate((case, case, case, case), repeat_critical=1)

    first = report["cases"][0]
    assert report["decision"] == "hold"
    assert report["summary"]["evaluated_scored_runs"] == 4
    assert report["summary"]["schema_validity_rate"] == 1
    assert report["summary"]["tokens"] == {
        "input_total": 28,
        "output_total": 12,
    }
    assert first["failure_phase"] == "connected_execution"
    assert first["error_codes"] == ["connected_execution.value_error"]
    assert first["connected_stop"] == "failed"
    assert first["actual_route"] != "planner_error"
    assert all("credential" not in json.dumps(row) for row in report["cases"])


@pytest.mark.asyncio
async def test_plan_gate_type_error_is_not_planner_schema_failure(monkeypatch) -> None:
    case = load_scenarios(FIXTURE)[0]

    async def planner(_text, **_kwargs):
        return _plan()

    def fail_gate(*_args, **_kwargs):
        raise TypeError("raw gate detail")

    monkeypatch.setattr(scenario_eval.PlanGate, "evaluate", fail_gate)
    report = await ScenarioEvaluator(
        catalog=_catalog(), router=object(), planner=planner
    ).evaluate((case, case, case, case), repeat_critical=1)

    assert report["summary"]["evaluated_scored_runs"] == 4
    assert report["summary"]["schema_validity_rate"] == 1
    assert {row["failure_phase"] for row in report["cases"]} == {"plan_gate"}
    assert {tuple(row["error_codes"]) for row in report["cases"]} == {
        ("plan_gate.type_error",)
    }


@pytest.mark.asyncio
async def test_score_value_error_has_separate_sanitized_phase(monkeypatch) -> None:
    case = load_scenarios(FIXTURE)[0]

    async def planner(_text, **_kwargs):
        return _plan()

    def fail_score(*_args, **_kwargs):
        raise ValueError("raw score detail")

    monkeypatch.setattr(scenario_eval, "score_plan", fail_score)
    report = await ScenarioEvaluator(
        catalog=_catalog(), router=object(), planner=planner
    ).evaluate((case,), repeat_critical=1)

    row = report["cases"][0]
    assert report["summary"]["schema_validity_rate"] == 1
    assert row["failure_phase"] == "score"
    assert row["error_codes"] == ["score.value_error"]
    assert "raw score detail" not in json.dumps(report)


@pytest.mark.asyncio
async def test_contract_classification_error_does_not_advance_schema_stop(
    monkeypatch,
) -> None:
    case = load_scenarios(FIXTURE)[0]

    async def planner(_text, **_kwargs):
        return _plan()

    def fail_classification(*_args, **_kwargs):
        raise ValueError("raw contract detail")

    monkeypatch.setattr(scenario_eval, "classify_contract", fail_classification)
    report = await ScenarioEvaluator(
        catalog=_catalog(), router=object(), planner=planner
    ).evaluate((case, case, case, case), repeat_critical=1)

    assert report["summary"]["evaluated_scored_runs"] == 4
    assert report["summary"]["schema_validity_rate"] == 1
    assert {row["failure_phase"] for row in report["cases"]} == {
        "contract_classification"
    }
    assert {tuple(row["error_codes"]) for row in report["cases"]} == {
        ("contract_classification.value_error",)
    }


@pytest.mark.asyncio
async def test_connected_side_effect_still_aborts_immediately_with_exact_counts() -> (
    None
):
    case = load_scenarios(FIXTURE)[4]
    asset = _asset()

    async def planner(_text, **_kwargs):
        return _plan(asset=AssetRef("skill", asset.name))

    async def connected_executor(_case, _plan, _assets):
        return "completed", SideEffectCounts(cron_notifier=1)

    evaluator = ScenarioEvaluator(
        catalog=_catalog(asset),
        router=object(),
        planner=planner,
        execute_read_only_contract_assets=True,
        connected_executor=connected_executor,
    )
    with pytest.raises(SideEffectDetected, match="side_effect_detected"):
        await evaluator.evaluate((case,), repeat_critical=1)

    assert evaluator.guard.counts == SideEffectCounts(cron_notifier=1)


def test_fixture_rejects_duplicate_id(tmp_path: Path) -> None:
    first = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "duplicate.jsonl"
    path.write_text(first + "\n" + first + "\n", encoding="utf-8")

    with pytest.raises(ScenarioFixtureError, match="duplicate"):
        load_scenarios(path, expected_count=None)


def test_fixture_rejects_unknown_route(tmp_path: Path) -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[0])
    raw["expected"]["v4_route"] = "magic"
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    with pytest.raises(ScenarioFixtureError, match="unknown route"):
        load_scenarios(path, expected_count=None)


@pytest.mark.parametrize(
    ("plan", "route"),
    [
        (_plan(), "simple_conversation"),
        (_plan(asset=AssetRef("recipe", "reader")), "recipe"),
        (
            _plan(
                mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
                asset=AssetRef("skill", "reader"),
            ),
            "react",
        ),
        (_plan(mode=ExecutionMode.RESOLVE_COMPLEX_PROBLEM), "deep_research"),
        (_plan(mode=ExecutionMode.CLARIFY, clarify=True), "interrupt"),
    ],
)
def test_route_normalization(plan: UnifiedTurnPlan, route: str) -> None:
    assert normalize_v4_route(plan) == route


def test_aggregate_includes_confusion_and_critical_stability() -> None:
    case = load_scenarios(FIXTURE)[0]
    row = score_plan(
        case,
        _plan(),
        PlanGateResult(GateStatus.PASS, _plan()),
        _catalog(),
    )
    report = aggregate_results(
        [row, replace(row, repeat_index=2)],
        provider_calls=2,
        provider_call_budget=64,
        provider_backend="test",
        provider_model="model",
        side_effect_counts=SideEffectCounts(),
        elapsed_seconds=1,
    )

    assert report["route_confusion"] == {
        "simple_conversation": {"simple_conversation": 2}
    }
    assert report["summary"]["pass_rate"] == 1


def test_provider_budget_fails_before_excess_call() -> None:
    budget = ProviderCallBudget(1)
    budget.reserve()

    with pytest.raises(ProviderBudgetExceeded):
        budget.reserve()

    assert budget.used == 1


def test_report_rejects_raw_and_credential_fields() -> None:
    with pytest.raises(ValueError, match="forbidden report field"):
        assert_sanitized_report({"standalone_question": "raw"})
    with pytest.raises(ValueError, match="credential-like"):
        assert_sanitized_report({"safe": "api_key=abcdefgh"})


def test_contract_gap_and_complete_read_only_are_separate() -> None:
    incomplete = _asset("gap", output_contract=None)
    complete = _asset("ok")
    catalog = _catalog(incomplete, complete)

    assert (
        classify_contract(catalog, (AssetRef("skill", "missing"),)).status
        == "contract_coverage_gap"
    )
    assert (
        classify_contract(catalog, (AssetRef("skill", "gap"),)).error_code
        == "contract.incomplete"
    )
    assert (
        classify_contract(catalog, (AssetRef("skill", "ok"),)).status
        == "read_only_complete"
    )


def test_contract_requires_every_exact_asset_identity_to_be_safe() -> None:
    catalog = _catalog(
        _asset("ok"),
        _asset("bad", output_contract=None),
        _asset("mutating", read_only=False, side_effects=True),
    )

    incomplete = classify_contract(
        catalog,
        (AssetRef("skill", "ok"), AssetRef("skill", "bad")),
    )
    wrong_type = classify_contract(catalog, (AssetRef("recipe", "ok"),))
    mutating = classify_contract(
        catalog,
        (AssetRef("skill", "ok"), AssetRef("skill", "mutating")),
    )

    assert incomplete.status == "contract_coverage_gap"
    assert incomplete.asset_name == "ok"
    assert incomplete.issues == (ContractIssue("skill", "bad", "contract.incomplete"),)
    assert wrong_type.status == "contract_coverage_gap"
    assert wrong_type.issues == (
        ContractIssue("recipe", "ok", "contract.asset_missing"),
    )
    assert mutating.status == "dispatch_denied"
    assert mutating.issues == (
        ContractIssue("skill", "mutating", "contract.not_read_only"),
    )


def test_go_fails_closed_for_consistently_wrong_critical_rows() -> None:
    case = load_scenarios(FIXTURE)[0]
    row = score_plan(
        case,
        _plan(),
        PlanGateResult(GateStatus.PASS, _plan()),
        _catalog(),
    )
    adversarial = replace(
        row,
        critical=True,
        checks={
            "route": True,
            "execution_mode": True,
            "context_relation": True,
            "gate": False,
            "asset": False,
        },
        contract_status="contract_coverage_gap",
        contract_issues=(ContractIssue("skill", "bad", "contract.incomplete"),),
        connected_stop="rollback_required",
        connected_required=True,
    )
    report = aggregate_results(
        [replace(adversarial, repeat_index=index) for index in range(1, 4)],
        provider_calls=3,
        provider_call_budget=64,
        provider_backend="test",
        provider_model="model",
        side_effect_counts=SideEffectCounts(),
        elapsed_seconds=1,
    )

    assert report["decision"] == "hold"
    assert report["summary"]["critical_pass_rate"] == 0
    assert report["summary"]["critical_stability_rate"] == 1
    assert report["summary"]["route_mode_context_macro_pass_rate"] == 1
    assert report["summary"]["rollback_required_count"] == 3
    assert report["summary"]["connected_completed_count"] == 0
    assert report["contract_gaps"] == {"bad": 3}


def test_side_effect_observation_aborts_immediately() -> None:
    guard = SideEffectGuard()

    with pytest.raises(SideEffectDetected):
        guard.observe(SideEffectCounts(telegram_send=1))

    assert guard.counts.telegram_send == 1
