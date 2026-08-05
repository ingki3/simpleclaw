"""BIZ-578 32개 fixed-gold planner→PlanGate replay 통합 회귀."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.plan_gate import PlanGate
from simpleclaw.agent.planner_catalog import (
    PlannerAsset,
    PlannerCatalog,
    build_planner_catalog,
)
from simpleclaw.agent.resolution_types import (
    CapabilityCoverage,
    ComplexitySignal,
    ExecutionMode,
)
from simpleclaw.agent.tool_schemas import ToolScope, build_native_tool_registry
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
from simpleclaw.capability import parse_owned_contract_metadata
from simpleclaw.evaluation.langgraph_v4_scenario_eval import (
    ConnectedContractProbe,
    ScenarioCase,
    ScenarioEvaluator,
    SideEffectCounts,
    load_scenarios,
)
from simpleclaw.langgraph_v4_shadow_validation import _definitions
from simpleclaw.llm.models import LLMResponse
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/langgraph_v4_user_scenarios.jsonl"
PRODUCTION_SKILL_FIXTURES = ROOT / "tests/fixtures/production-skills"


def _sports_contract_definitions():
    recipe = next(
        item
        for item in discover_recipes(ROOT / "tests/fixtures/recipes")
        if item.name == "sports-live"
    )
    skill = next(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"), PRODUCTION_SKILL_FIXTURES
        )
        if item.name == "naver-sports-skill"
    )
    return recipe, skill


class _UnusedRouter:
    async def send(self, _request):  # pragma: no cover - replay planner owns output
        raise AssertionError("mock replay must not call a provider")


class _ReplayRouter:
    """structured provider boundary를 통과하는 단일 응답 대역."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def send(self, request):
        tool_schema = request.response_schema["properties"]["execution"]["properties"][
            "allowed_tools"
        ]
        assert set(tool_schema["items"]["enum"]) == {"execute_skill"}
        return LLMResponse(text=json.dumps(self.payload, ensure_ascii=False))


def _asset_type(name: str) -> str:
    if name in {"krstock", "usstock", "sports-live"}:
        return "recipe"
    if name == "cron":
        return "native_tool"
    return "skill"


def _catalog(cases: tuple[ScenarioCase, ...]) -> PlannerCatalog:
    names = sorted({name for case in cases for name in case.expected.acceptable_assets})
    assets = []
    for name in names:
        mutation = name == "cron"
        assets.append(
            PlannerAsset(
                asset_type=_asset_type(name),
                name=name,
                description="fixed gold replay asset",
                domains=(),
                intents=(),
                read_only=not mutation,
                side_effects=mutation,
                freshness_sensitive=False,
                direct_answer=True,
                requires_confirmation=mutation,
                output_contract="asset_result.v1",
                declared=True,
                runtime_visible=True,
                coverage="full_coverage",
                input_contract="query.v1",
            )
        )
    return PlannerCatalog(tuple(assets), "scenario-catalog-v1")


def _selected_asset(case: ScenarioCase) -> str | None:
    if not case.expected.acceptable_assets:
        return None
    if case.expected.side_effect_policy == "confirmation_before_dispatch":
        return "cron"
    if case.expected.v4_route == "deep_research":
        return next(
            (
                name
                for name in reversed(case.expected.acceptable_assets)
                if _asset_type(name) != "recipe"
            ),
            case.expected.acceptable_assets[-1],
        )
    return case.expected.acceptable_assets[0]


def _gold_plan(case: ScenarioCase, catalog: PlannerCatalog) -> UnifiedTurnPlan:
    asset_name = _selected_asset(case)
    asset = (
        None if asset_name is None else AssetRef(_asset_type(asset_name), asset_name)
    )
    relation = ContextRelation(case.expected.context_relations[0])
    mode = ExecutionMode(case.expected.execution_modes[0])
    fact_required = case.expected.fact_required
    confirmation = case.expected.side_effect_policy == "confirmation_before_dispatch"
    standalone = " ".join((*case.expected.required_terms, case.current))
    return UnifiedTurnPlan(
        original_text=case.current,
        context=ContextSelection(
            relation=relation,
            use_prior_context=bool(case.expected.selected_turn_ids),
            selected_turn_ids=case.expected.selected_turn_ids,
            standalone_question=standalone,
        ),
        clarification=ClarificationPlan(
            required=case.expected.clarification_required,
            question="진행 전에 확인이 필요합니다. 계속할까요?"
            if case.expected.clarification_required
            else "",
        ),
        domains=(case.category,) if fact_required else (),
        intents=("current_result",) if fact_required else (),
        fact_check=FactCheckPlan(
            required=fact_required,
            owner=EvidenceOwner.ASSET if fact_required else EvidenceOwner.NONE,
            domain=case.category if fact_required else "none",
            entities=(),
            search_query=standalone if fact_required else "",
            intents=("current_result",) if fact_required else (),
            reference_date="2026-08-05" if fact_required else "",
            required_claims=("current result",) if fact_required else (),
            freshness_required=fact_required,
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=asset,
            allowed_assets=() if asset is None else (asset,),
            requires_confirmation=confirmation,
            complexity_signals=(
                (ComplexitySignal.BRANCHING_PLAN,)
                if mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM
                else ()
            ),
        ),
        capability=CapabilityPlan(
            coverage=(
                CapabilityCoverage.NO_MATCH
                if asset is None
                else CapabilityCoverage.FULL
            ),
            primary_asset=asset,
            supporting_assets=() if asset is None else (asset,),
        ),
        confidence=1,
        decision_summary="fixed gold replay",
        catalog_fingerprint=catalog.fingerprint,
    )


def _structured_recipe_payload(case: ScenarioCase) -> dict[str, object]:
    """fq-05~07 exact sports recipe를 provider schema payload로 표현한다."""
    current = case.current
    return {
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": current,
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
            "reference_date": "2026-08-05",
            "search_query": current,
            "required_claims": list(case.expected.required_terms),
            "freshness_required": True,
            "reason": "current sports result",
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
            "mode": "direct_answer",
            "allowed_tools": [],
            "requires_confirmation": False,
            "complexity_signals": [],
            "reason": "recipe first",
        },
        "confidence": 1,
        "decision_summary": "sports recipe replay",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", (4, 5, 6))
async def test_corrected_schema_failure_cases_cross_structured_boundary(
    case_index: int,
) -> None:
    """authoritative v2의 fq-05~07은 schema-valid exact recipe plan을 보존한다."""
    case = load_scenarios(FIXTURE)[case_index]
    base_catalog = _catalog((case,))
    catalog = PlannerCatalog(
        (
            PlannerAsset(
                asset_type="native_tool",
                name="execute_skill",
                description="exact skill adapter",
                domains=(),
                intents=(),
                read_only=True,
                side_effects=False,
                freshness_sensitive=False,
                direct_answer=False,
                requires_confirmation=False,
                output_contract=None,
                declared=True,
                runtime_visible=True,
            ),
            *base_catalog.assets,
        ),
        base_catalog.fingerprint,
    )
    candidates = ContextCandidateSet(candidates=(), total_chars=0, truncated=False)
    plan = await plan_turn_with_llm(
        case.current,
        candidates=candidates,
        catalog=catalog,
        router=_ReplayRouter(_structured_recipe_payload(case)),
    )
    gate = PlanGate().evaluate(plan, candidates=candidates, catalog=catalog)

    assert plan.capability.primary_asset == AssetRef("recipe", "sports-live")
    assert plan.execution.allowed_tools == ()
    assert gate.status.value == "pass"


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", (4, 5, 6))
async def test_exact_sports_recipe_completes_connected_read_only_probe(
    tmp_path: Path,
    case_index: int,
) -> None:
    """fq-05~07 exact Recipe는 owned contract로 rollback 없이 완료된다."""
    recipe, target_skill = _sports_contract_definitions()
    discovered = build_planner_catalog(
        skills=(target_skill,),
        recipes=(recipe,),
        native_specs=(),
    )
    catalog = PlannerCatalog(
        (
            PlannerAsset(
                asset_type="native_tool",
                name="execute_skill",
                description="exact skill adapter",
                domains=(),
                intents=(),
                read_only=True,
                side_effects=False,
                freshness_sensitive=False,
                direct_answer=False,
                requires_confirmation=False,
                output_contract=None,
                declared=True,
                runtime_visible=True,
            ),
            *discovered.assets,
        ),
        discovered.fingerprint,
    )
    case = load_scenarios(FIXTURE)[case_index]
    candidates = ContextCandidateSet(candidates=(), total_chars=0, truncated=False)
    plan = await plan_turn_with_llm(
        case.current,
        candidates=candidates,
        catalog=catalog,
        router=_ReplayRouter(_structured_recipe_payload(case)),
    )
    gate = PlanGate().evaluate(plan, candidates=candidates, catalog=catalog)
    asset = next(
        item
        for item in catalog.assets
        if (item.asset_type, item.name) == ("recipe", "sports-live")
    )
    delegate_asset = next(
        item
        for item in catalog.assets
        if (item.asset_type, item.name) == ("skill", "naver-sports-skill")
    )
    plan = replace(
        plan,
        capability=replace(
            plan.capability,
            supporting_assets=(AssetRef("skill", "naver-sports-skill"),),
        ),
        execution=replace(
            plan.execution,
            allowed_assets=(
                AssetRef("recipe", "sports-live"),
                AssetRef("skill", "naver-sports-skill"),
            ),
        ),
    )
    probe = ConnectedContractProbe(
        definitions=(recipe, target_skill),
        directory=tmp_path / f"case-{case_index}",
    )
    try:
        stop, counts = await probe(case, plan, (asset, delegate_asset))
    finally:
        probe.close()

    assert gate.status.value == "pass"
    assert stop == "completed", probe.last_rollback_reasons
    assert probe.last_rollback_reasons == ()
    assert counts == SideEffectCounts()


@pytest.mark.asyncio
async def test_fq17_shopping_plan_completes_with_registry_schema_example(
    tmp_path: Path,
) -> None:
    shopping = next(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"), PRODUCTION_SKILL_FIXTURES
        )
        if item.name == "naver-shopping-skill"
    )
    assert shopping.input_contract is not None
    examples = shopping.input_contract.json_schema.get("examples")
    assert isinstance(examples, list) and len(examples) == 1
    example = examples[0]
    assert isinstance(example, dict)
    args = example.get("args")
    assert isinstance(args, str)
    catalog = build_planner_catalog(skills=(shopping,), native_specs=())
    asset = next(item for item in catalog.assets if item.name == shopping.name)
    ref = AssetRef("skill", shopping.name)
    case = load_scenarios(FIXTURE)[16]
    base = _gold_plan(case, catalog)
    plan = replace(
        base,
        context=replace(base.context, standalone_question=args),
        execution=replace(
            base.execution,
            primary_asset=ref,
            allowed_assets=(ref,),
        ),
        capability=replace(
            base.capability,
            primary_asset=ref,
            supporting_assets=(ref,),
        ),
        catalog_fingerprint=catalog.fingerprint,
    )
    probe = ConnectedContractProbe(
        definitions=(shopping,),
        directory=tmp_path / "fq17-shopping",
    )
    try:
        stop, counts = await probe(case, plan, (asset,))
    finally:
        probe.close()

    assert stop == "completed", probe.last_rollback_reasons
    assert probe.last_rollback_reasons == ()
    assert counts == SideEffectCounts()


@pytest.mark.asyncio
async def test_all_32_scenarios_cross_planner_gate_and_no_send_boundaries() -> None:
    cases = load_scenarios(FIXTURE)
    catalog = _catalog(cases)
    by_key = {
        (case.current, tuple(turn.id for turn in case.history)): case for case in cases
    }
    connected: list[str] = []
    planned: list[str] = []

    async def planner(text, *, candidates, catalog, **_kwargs):
        key = (text, tuple(item.turn_id for item in candidates.candidates))
        case = by_key[key]
        planned.append(case.id)
        return _gold_plan(case, catalog)

    async def connected_executor(case, _plan, assets):
        assert all(asset.read_only is True for asset in assets)
        assert all(asset.side_effects is False for asset in assets)
        connected.append(case.id)
        return "completed", SideEffectCounts()

    evaluator = ScenarioEvaluator(
        catalog=catalog,
        router=_UnusedRouter(),
        planner=planner,
        execute_read_only_contract_assets=True,
        connected_executor=connected_executor,
        connected_executor_kind="synthetic_contract",
        ingress_recipe_names=("krstock", "usstock"),
    )
    report = await evaluator.evaluate(cases, repeat_critical=3)

    assert report["summary"]["runs"] == 39
    assert report["summary"]["unique_cases"] == 25
    assert report["summary"]["total_runs"] == 46
    assert report["summary"]["total_inventory_cases"] == 32
    assert report["summary"]["not_scored_cases"] == 7
    assert report["summary"]["schema_validity_rate"] == 1
    assert report["summary"]["critical_stability_rate"] == 1
    assert report["side_effect_counts"] == {
        "telegram_send": 0,
        "cron_notifier": 0,
        "conversation_write": 0,
    }
    assert report["decision"] == "go", (
        report["summary"],
        [
            (row["case_id"], row["failed_checks"], row["error_codes"])
            for row in report["cases"]
            if row["critical"] and row["scored"] and not row["passed"]
        ],
    )
    assert report["not_scored_inventory"] == {
        "attachment_scope_gap": 1,
        "ingress_bypass": 4,
        "operator_scope_gap": 2,
    }
    assert report["connected_executor_kinds"] == ["synthetic_contract"]
    assert all(
        case_id not in planned
        for case_id in ("fq-02", "fq-03", "fq-04", "fq-23", "fq-27", "fq-28", "fq-29")
    )
    ingress = {
        row["case_id"]: row
        for row in report["cases"]
        if row["evaluation_scope"] == "ingress_bypass"
    }
    assert ingress["fq-03"]["actual_route"] == "recipe_command_bypass"
    assert ingress["fq-04"]["actual_route"] == "recipe_command_bypass"
    assert all(row["planner_called"] is False for row in ingress.values())
    assert all(row["passed"] is None for row in ingress.values())
    assert "fq-25" not in connected
    assert all(
        row["connected_stop"] != "completed"
        for row in report["cases"]
        if row["case_id"] in {"fq-23", "fq-28", "fq-29"}
    )
    by_case = {row["case_id"]: row for row in report["cases"]}
    for case_id in ("fq-22", "fq-25", "fq-30", "fq-31", "fq-32"):
        assert by_case[case_id]["passed"] is True
    assert by_case["fq-23"]["passed"] is None
    image_case = next(case for case in cases if case.id == "fq-23")
    assert image_case.expected.context_relations == ("same_thread",)
    assert image_case.expected.selected_turn_ids == ("m23u",)
    assert by_case["fq-25"]["gate_status"] == "confirmation_required"
    assert by_case["fq-31"]["gate_status"] == "clarify"
    assert by_case["fq-32"]["gate_status"] == "clarify"


@pytest.mark.asyncio
async def test_mutation_case_interrupts_before_dispatch() -> None:
    case = load_scenarios(FIXTURE)[24]
    catalog = _catalog((case,))
    dispatched = False

    async def planner(_text, **_kwargs):
        return _gold_plan(case, catalog)

    async def connected_executor(_case, _plan, _assets):
        nonlocal dispatched
        dispatched = True
        return "completed", SideEffectCounts()

    report = await ScenarioEvaluator(
        catalog=catalog,
        router=_UnusedRouter(),
        planner=planner,
        execute_read_only_contract_assets=True,
        connected_executor=connected_executor,
    ).evaluate((case,))

    assert dispatched is False
    assert report["cases"][0]["actual_route"] == "interrupt"
    assert report["cases"][0]["gate_status"] == "confirmation_required"


@pytest.mark.asyncio
async def test_contract_complete_asset_enters_connected_v4_graph(
    tmp_path: Path,
) -> None:
    definitions = _definitions()
    catalog = build_planner_catalog(
        skills=tuple(
            item for item in definitions if item.contract_asset_type == "skill"
        ),
        recipes=tuple(
            item for item in definitions if item.contract_asset_type == "recipe"
        ),
        native_specs=(),
    )
    ref = AssetRef("skill", "contract-fixture-step")
    plan = UnifiedTurnPlan(
        original_text="connected fixture",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="connected-value",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("fixture",),
        intents=("verify",),
        fact_check=FactCheckPlan(
            required=False,
            owner=EvidenceOwner.NONE,
            domain="fixture",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            primary_asset=ref,
            allowed_assets=(ref,),
        ),
        capability=CapabilityPlan(
            coverage=CapabilityCoverage.PARTIAL,
            primary_asset=ref,
            supporting_assets=(ref,),
        ),
        confidence=1,
        decision_summary="connected fixture",
        catalog_fingerprint=catalog.fingerprint,
    )
    case = load_scenarios(FIXTURE)[9]
    asset = next(item for item in catalog.assets if item.name == ref.name)
    probe = ConnectedContractProbe(definitions=definitions, directory=tmp_path)
    try:
        stop, counts = await probe(case, plan, (asset,))
    finally:
        probe.close()

    assert stop == "completed", probe.last_rollback_reasons
    assert counts == SideEffectCounts()


@pytest.mark.asyncio
async def test_incomplete_supporting_asset_blocks_connected_dispatch() -> None:
    case = load_scenarios(FIXTURE)[9]
    base_catalog = _catalog((case,))
    bad = PlannerAsset(
        asset_type="skill",
        name="bad-support",
        description="incomplete supporting asset",
        domains=(),
        intents=(),
        read_only=True,
        side_effects=False,
        freshness_sensitive=False,
        direct_answer=True,
        requires_confirmation=False,
        output_contract=None,
        declared=True,
        runtime_visible=True,
        coverage="full_coverage",
        input_contract="query.v1",
    )
    catalog = PlannerCatalog((*base_catalog.assets, bad), base_catalog.fingerprint)
    bad_ref = AssetRef("skill", bad.name)
    dispatched = False

    async def planner(_text, **_kwargs):
        plan = _gold_plan(case, catalog)
        return replace(
            plan,
            capability=replace(
                plan.capability,
                supporting_assets=(*plan.capability.supporting_assets, bad_ref),
            ),
        )

    async def connected_executor(_case, _plan, _assets):
        nonlocal dispatched
        dispatched = True
        return "completed", SideEffectCounts()

    report = await ScenarioEvaluator(
        catalog=catalog,
        router=_UnusedRouter(),
        planner=planner,
        execute_read_only_contract_assets=True,
        connected_executor=connected_executor,
    ).evaluate((case,), repeat_critical=1)

    assert dispatched is False
    assert report["decision"] == "hold"
    assert report["cases"][0]["contract_status"] == "contract_coverage_gap"
    assert report["contract_gaps"] == {"bad-support": 1}


@pytest.mark.asyncio
async def test_connected_contract_failure_is_explicit_hold_not_planner_failure() -> (
    None
):
    case = load_scenarios(FIXTURE)[9]
    catalog = _catalog((case,))

    async def planner(_text, **_kwargs):
        return _gold_plan(case, catalog)

    async def connected_executor(_case, _plan, _assets):
        raise TypeError("connected raw contract detail")

    report = await ScenarioEvaluator(
        catalog=catalog,
        router=_UnusedRouter(),
        planner=planner,
        execute_read_only_contract_assets=True,
        connected_executor=connected_executor,
        connected_executor_kind="actual_contract",
    ).evaluate((case,), repeat_critical=1)

    row = report["cases"][0]
    assert report["decision"] == "hold"
    assert report["summary"]["schema_validity_rate"] == 1
    assert row["actual_route"] == case.expected.v4_route
    assert row["failure_phase"] == "connected_execution"
    assert row["error_codes"] == ["connected_execution.type_error"]
    assert row["connected_stop"] == "failed"
    assert "planner.schema_or_unavailable" not in row["error_codes"]
    assert "connected raw contract detail" not in json.dumps(report)


@pytest.mark.asyncio
async def test_production_read_only_assets_complete_connected_read_only_probe(
    tmp_path: Path,
) -> None:
    target_skills = {
        "gmail-skill",
        "google-news-search-skill",
        "kr-stock-skill",
        "naver-shopping-skill",
        "naver-sports-skill",
        "us-stock-skill",
    }
    target_native = {"web_fetch", "web_search"}
    skills = tuple(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"), PRODUCTION_SKILL_FIXTURES
        )
        if item.name in target_skills
    )
    assert {item.name for item in skills} == target_skills
    native_specs = tuple(
        spec
        for spec in build_native_tool_registry(scopes=(ToolScope.RUNTIME,))
        if spec.name in target_native
    )
    definitions = (*skills, *native_specs)
    catalog = build_planner_catalog(skills=skills, native_specs=native_specs)
    case = load_scenarios(FIXTURE)[9]
    probe = ConnectedContractProbe(definitions=definitions, directory=tmp_path)
    try:
        for asset_type, name in (
            ("skill", "gmail-skill"),
            ("skill", "google-news-search-skill"),
            ("skill", "kr-stock-skill"),
            ("skill", "naver-shopping-skill"),
            ("skill", "naver-sports-skill"),
            ("skill", "us-stock-skill"),
            ("native_tool", "web_fetch"),
            ("native_tool", "web_search"),
        ):
            ref = AssetRef(asset_type, name)
            base = _gold_plan(case, catalog)
            definition = next(
                (
                    item
                    for item in skills
                    if item.name == name and item.input_contract is not None
                ),
                None,
            )
            if definition is not None:
                examples = definition.input_contract.json_schema.get("examples")
                assert isinstance(examples, list) and len(examples) == 1
                example = examples[0]
                assert isinstance(example, dict)
                standalone = example["args"]
                assert isinstance(standalone, str)
            else:
                standalone = {
                    "web_fetch": "https://example.com/report",
                    "web_search": "시장 마감",
                }[name]
            plan = replace(
                base,
                context=replace(base.context, standalone_question=standalone),
                execution=replace(
                    base.execution,
                    primary_asset=ref,
                    allowed_assets=(ref,),
                ),
                capability=replace(
                    base.capability,
                    primary_asset=ref,
                    supporting_assets=(ref,),
                ),
                catalog_fingerprint=catalog.fingerprint,
            )
            asset = next(
                item
                for item in catalog.assets
                if (item.asset_type, item.name) == (asset_type, name)
            )

            stop, counts = await probe(case, plan, (asset,))

            assert stop == "completed", (name, probe.last_rollback_reasons)
            assert counts == SideEffectCounts()
    finally:
        probe.close()


@pytest.mark.asyncio
async def test_supporting_web_assets_complete_as_sequential_connected_probes(
    tmp_path: Path,
) -> None:
    native_specs = tuple(
        spec
        for spec in build_native_tool_registry(scopes=(ToolScope.RUNTIME,))
        if spec.name in {"web_fetch", "web_search"}
    )
    catalog = build_planner_catalog(native_specs=native_specs)
    refs = (
        AssetRef("native_tool", "web_search"),
        AssetRef("native_tool", "web_fetch"),
    )
    base = _gold_plan(load_scenarios(FIXTURE)[9], catalog)
    plan = replace(
        base,
        context=replace(
            base.context, standalone_question="https://example.com/report"
        ),
        execution=replace(
            base.execution,
            primary_asset=None,
            allowed_assets=refs,
        ),
        capability=replace(
            base.capability,
            primary_asset=None,
            supporting_assets=refs,
        ),
        catalog_fingerprint=catalog.fingerprint,
    )
    probe = ConnectedContractProbe(definitions=native_specs, directory=tmp_path)
    try:
        stop, counts = await probe(
            load_scenarios(FIXTURE)[9], plan, catalog.assets
        )
    finally:
        probe.close()

    assert stop == "completed", probe.last_rollback_reasons
    assert probe.last_rollback_reasons == ()
    assert counts == SideEffectCounts()


@pytest.mark.asyncio
async def test_cli_skill_uses_declared_example_when_question_is_not_contract_valid(
    tmp_path: Path,
) -> None:
    skill = next(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"), PRODUCTION_SKILL_FIXTURES
        )
        if item.name == "google-news-search-skill"
    )
    assert skill.input_contract is not None
    schema = skill.input_contract.json_schema
    schema["properties"]["args"]["examples"] = [
        '--query "시장 마감" --format json'
    ]
    skill = replace(
        skill,
        input_contract=parse_owned_contract_metadata(
            {
                "contract_id": skill.input_contract.contract_id,
                "version": skill.input_contract.version,
                "owner_ref": {
                    "type": skill.input_contract.owner_type,
                    "name": skill.input_contract.owner_name,
                },
                "json_schema": schema,
            },
            source="declared CLI example fixture",
        ),
    )
    catalog = build_planner_catalog(skills=(skill,), native_specs=())
    ref = AssetRef("skill", skill.name)
    base = _gold_plan(load_scenarios(FIXTURE)[9], catalog)
    plan = replace(
        base,
        context=replace(
            base.context,
            standalone_question="오늘 시장 마감 뉴스를 정리해 주세요",
        ),
        execution=replace(
            base.execution, primary_asset=ref, allowed_assets=(ref,)
        ),
        capability=replace(
            base.capability, primary_asset=ref, supporting_assets=()
        ),
        catalog_fingerprint=catalog.fingerprint,
    )
    probe = ConnectedContractProbe(definitions=(skill,), directory=tmp_path)
    selected_assets = tuple(
        item for item in catalog.assets if item.name == skill.name
    )
    try:
        stop, counts = await probe(
            load_scenarios(FIXTURE)[9], plan, selected_assets
        )
    finally:
        probe.close()

    assert stop == "completed", probe.last_rollback_reasons
    assert probe.last_rollback_reasons == ()
    assert counts == SideEffectCounts()


@pytest.mark.asyncio
async def test_cli_skill_without_declared_fallback_fails_closed(
    tmp_path: Path,
) -> None:
    skill = next(
        item
        for item in discover_skills(
            Path("/__missing_local_skills__"), PRODUCTION_SKILL_FIXTURES
        )
        if item.name == "google-news-search-skill"
    )
    assert skill.input_contract is not None
    schema = json.loads(json.dumps(skill.input_contract.json_schema))
    schema.pop("default", None)
    schema.pop("examples", None)
    for property_schema in schema.get("properties", {}).values():
        property_schema.pop("default", None)
        property_schema.pop("examples", None)
    skill = replace(
        skill,
        input_contract=parse_owned_contract_metadata(
            {
                "contract_id": skill.input_contract.contract_id,
                "version": skill.input_contract.version,
                "owner_ref": {
                    "type": skill.input_contract.owner_type,
                    "name": skill.input_contract.owner_name,
                },
                "json_schema": schema,
            },
            source="missing declared CLI fallback fixture",
        ),
    )
    catalog = build_planner_catalog(skills=(skill,), native_specs=())
    ref = AssetRef("skill", skill.name)
    base = _gold_plan(load_scenarios(FIXTURE)[9], catalog)
    plan = replace(
        base,
        context=replace(base.context, standalone_question="자연어 질문"),
        execution=replace(
            base.execution, primary_asset=ref, allowed_assets=(ref,)
        ),
        capability=replace(
            base.capability, primary_asset=ref, supporting_assets=()
        ),
        catalog_fingerprint=catalog.fingerprint,
    )
    probe = ConnectedContractProbe(definitions=(skill,), directory=tmp_path)
    selected_assets = tuple(
        item for item in catalog.assets if item.name == skill.name
    )
    try:
        with pytest.raises(ValueError, match="payload.safe_example_missing"):
            await probe(load_scenarios(FIXTURE)[9], plan, selected_assets)
    finally:
        probe.close()
