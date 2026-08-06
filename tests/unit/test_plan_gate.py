"""BIZ-492 — UnifiedTurnPlan의 로컬 실행 gate 계약."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
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


def _asset(
    name: str = "weather",
    *,
    asset_type: str = "skill",
    declared: bool = True,
    read_only: bool = True,
    side_effects: bool = False,
    requires_confirmation: bool = False,
    freshness_sensitive: bool = False,
    domains: tuple[str, ...] = (),
    intents: tuple[str, ...] = (),
    connected_contract: bool = True,
) -> PlannerAsset:
    owner = f"{asset_type}:{name}" if connected_contract else None
    return PlannerAsset(
        asset_type=asset_type,
        name=name,
        description="test asset",
        domains=domains,
        intents=intents,
        read_only=read_only,
        side_effects=side_effects,
        freshness_sensitive=freshness_sensitive,
        direct_answer=True,
        requires_confirmation=requires_confirmation,
        declared=declared,
        runtime_visible=True,
        coverage="full_coverage",
        input_contract="query.v1",
        output_contract="asset_result.v1",
        contract_owner=owner,
        input_contract_ref=(f"{asset_type}.{name}.input@1" if owner else None),
        output_contract_ref=(f"{asset_type}.{name}.output@1" if owner else None),
        input_schema_hash=("i" * 64 if owner else None),
        output_schema_hash=("o" * 64 if owner else None),
        binding_identity=("binding:" + "b" * 64 if owner else None),
        definition_fingerprint=("d" * 64 if owner else None),
    )


def _catalog(*assets: PlannerAsset) -> PlannerCatalog:
    return PlannerCatalog(assets=tuple(assets), fingerprint="catalog-v1")


def _candidates() -> ContextCandidateSet:
    candidates = (
        ContextCandidate(
            turn_id="msg:1",
            role="user",
            timestamp=datetime.now(UTC),
            content="a" * 6,
            trust=ContextTrust.USER_INPUT,
        ),
        ContextCandidate(
            turn_id="msg:2",
            role="assistant",
            timestamp=datetime.now(UTC),
            content="b" * 5,
            trust=ContextTrust.ASSISTANT_CONTEXT_ONLY,
        ),
    )
    return ContextCandidateSet(candidates=candidates, total_chars=11, truncated=False)


def _plan(
    *,
    relation: ContextRelation = ContextRelation.STANDALONE,
    use_prior_context: bool = False,
    selected_turn_ids: tuple[str, ...] = (),
    unresolved_references: tuple[str, ...] = (),
    clarification_required: bool = False,
    mode: ExecutionMode = ExecutionMode.DIRECT_ANSWER,
    primary_asset: AssetRef | None = None,
    allowed_assets: tuple[AssetRef, ...] = (),
    allowed_tools: tuple[str, ...] = (),
    requires_confirmation: bool = False,
    fact_required: bool = False,
    owner: EvidenceOwner = EvidenceOwner.NONE,
    search_query: str = "",
    intents: tuple[str, ...] | None = None,
    freshness_required: bool | None = None,
) -> UnifiedTurnPlan:
    resolved_intents = intents if intents is not None else (
        ("current_weather",) if fact_required else ()
    )
    return UnifiedTurnPlan(
        original_text="질문",
        context=ContextSelection(
            relation=relation,
            use_prior_context=use_prior_context,
            selected_turn_ids=selected_turn_ids,
            standalone_question="독립 질문",
            unresolved_references=unresolved_references,
        ),
        clarification=ClarificationPlan(
            required=clarification_required,
            question="무엇을 뜻하시나요?" if clarification_required else "",
        ),
        domains=("weather",) if fact_required else (),
        intents=resolved_intents,
        fact_check=FactCheckPlan(
            required=fact_required,
            owner=owner,
            domain="weather" if fact_required else "none",
            entities=(),
            search_query=search_query,
            intents=resolved_intents,
            required_claims=("current conditions",) if fact_required else (),
            freshness_required=(
                fact_required
                if freshness_required is None
                else freshness_required
            ),
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=primary_asset,
            allowed_assets=allowed_assets,
            allowed_tools=allowed_tools,
            requires_confirmation=requires_confirmation,
            reason="test",
        ),
        confidence=0.9,
        decision_summary="test",
        catalog_fingerprint="catalog-v1",
    )


@pytest.mark.parametrize(
    ("plan", "code"),
    [
        (
            _plan(
                relation=ContextRelation.STANDALONE,
                use_prior_context=True,
                selected_turn_ids=("msg:1",),
            ),
            "context.standalone_has_prior_context",
        ),
        (
            _plan(
                relation=ContextRelation.SAME_THREAD,
                use_prior_context=True,
            ),
            "context.prior_context_without_selection",
        ),
        (
            _plan(
                relation=ContextRelation.SAME_THREAD,
                use_prior_context=True,
                selected_turn_ids=("invented",),
            ),
            "context.unknown_selected_turn",
        ),
    ],
)
def test_context_invariants_return_stable_repair_codes(
    plan: UnifiedTurnPlan,
    code: str,
) -> None:
    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.REPAIR
    assert code in {violation.code for violation in result.violations}
    assert result.effective_plan is None


@pytest.mark.parametrize(
    ("max_turns", "max_chars", "code"),
    [
        (1, 100, "context.selected_turn_limit_exceeded"),
        (8, 10, "context.selected_char_limit_exceeded"),
    ],
)
def test_selected_context_budgets_are_enforced(
    max_turns: int,
    max_chars: int,
    code: str,
) -> None:
    plan = _plan(
        relation=ContextRelation.SAME_THREAD,
        use_prior_context=True,
        selected_turn_ids=("msg:1", "msg:2"),
    )

    result = PlanGate(
        selected_context_max_turns=max_turns,
        selected_context_max_chars=max_chars,
    ).evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.REPAIR
    assert code in {violation.code for violation in result.violations}


def test_unresolved_reference_requires_clarification_mode() -> None:
    plan = _plan(
        relation=ContextRelation.UNCLEAR,
        unresolved_references=("그거",),
        clarification_required=False,
        mode=ExecutionMode.DIRECT_ANSWER,
    )

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.REPAIR
    assert {item.code for item in result.violations} >= {
        "clarification.required_for_unclear_context",
        "execution.clarify_mode_required",
    }


def test_valid_clarification_returns_clarify() -> None:
    plan = _plan(
        relation=ContextRelation.UNCLEAR,
        unresolved_references=("그거",),
        clarification_required=True,
        mode=ExecutionMode.CLARIFY,
    )

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.CLARIFY
    assert result.effective_plan is plan
    assert result.violations == ()


@pytest.mark.parametrize("text", ("다시 한 번 부탁해", "그래"))
def test_context_free_retry_and_ack_remain_clarifications(text: str) -> None:
    plan = replace(
        _plan(
            relation=ContextRelation.STANDALONE,
            clarification_required=True,
            mode=ExecutionMode.CLARIFY,
        ),
        original_text=text,
    )

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.CLARIFY
    assert result.effective_plan is plan


def test_retry_selects_only_original_user_turn() -> None:
    plan = _plan(
        relation=ContextRelation.SAME_THREAD,
        use_prior_context=True,
        selected_turn_ids=("msg:1",),
    )

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.PASS
    assert result.effective_plan is plan


@pytest.mark.parametrize(
    "relation",
    (ContextRelation.SAME_THREAD, ContextRelation.RELATED_REFERENCE),
)
def test_related_context_requires_exact_selected_turns(
    relation: ContextRelation,
) -> None:
    plan = _plan(relation=relation)

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.REPAIR
    assert "context.related_context_without_selection" in {
        item.code for item in result.violations
    }


def test_clarification_cannot_dispatch_as_direct_answer() -> None:
    plan = _plan(
        clarification_required=True,
        mode=ExecutionMode.DIRECT_ANSWER,
    )

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.REPAIR
    assert "execution.clarify_mode_required" in {
        item.code for item in result.violations
    }


def test_clarify_mode_requires_user_facing_clarification() -> None:
    plan = _plan(mode=ExecutionMode.CLARIFY)

    result = PlanGate().evaluate(plan, candidates=_candidates(), catalog=_catalog())

    assert result.status is GateStatus.REPAIR
    assert "clarification.required_for_clarify_mode" in {
        item.code for item in result.violations
    }


@pytest.mark.parametrize(
    ("mode", "fact_required", "owner", "search_query", "code"),
    [
        (
            ExecutionMode.ANSWER_WITH_EVIDENCE,
            False,
            EvidenceOwner.PLANNER,
            "서울 날씨",
            "fact_check.required",
        ),
        (
            ExecutionMode.ANSWER_WITH_EVIDENCE,
            True,
            EvidenceOwner.ASSET,
            "서울 날씨",
            "fact_check.planner_owner_required",
        ),
        (
            ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            True,
            EvidenceOwner.ASSET,
            "시장 비교",
            "fact_check.planner_owner_required",
        ),
        (
            ExecutionMode.ANSWER_WITH_EVIDENCE,
            True,
            EvidenceOwner.PLANNER,
            "",
            "fact_check.search_query_required",
        ),
    ],
)
def test_current_fact_invariants_fail_closed(
    mode: ExecutionMode,
    fact_required: bool,
    owner: EvidenceOwner,
    search_query: str,
    code: str,
) -> None:
    result = PlanGate().evaluate(
        _plan(
            mode=mode,
            fact_required=fact_required,
            owner=owner,
            search_query=search_query,
        ),
        candidates=_candidates(),
        catalog=_catalog(),
    )

    assert result.status is GateStatus.REPAIR
    assert code in {item.code for item in result.violations}


def test_unscoped_evidence_plan_repairs_unique_typed_exact_asset() -> None:
    sports = _asset(
        "sports-live",
        asset_type="recipe",
        freshness_sensitive=True,
        domains=("sports",),
        intents=("current_result",),
    )
    plan = replace(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="bounded sports query",
            intents=("current_result",),
        ),
        domains=("sports",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="sports",
            entities=(),
            search_query="bounded sports query",
            intents=("current_result",),
            reference_date="2026-08-02",
            required_claims=("score",),
            freshness_required=True,
        ),
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(sports),
    )

    assert result.status is GateStatus.PASS
    assert result.effective_plan is not None
    assert result.effective_plan.capability.primary_asset == AssetRef(
        "recipe", "sports-live"
    )
    assert result.effective_plan.capability.coverage.value == "full_coverage"
    assert result.effective_plan.fact_check.owner is EvidenceOwner.ASSET
    assert result.effective_plan.execution.allowed_tools == ()


def test_unscoped_evidence_plan_rejects_shorthand_only_exact_asset() -> None:
    sports = _asset(
        "sports-live",
        asset_type="recipe",
        freshness_sensitive=True,
        domains=("sports",),
        intents=("current_result",),
        connected_contract=False,
    )
    plan = replace(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="bounded sports query",
            intents=("current_result",),
        ),
        domains=("sports",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="sports",
            entities=(),
            search_query="bounded sports query",
            intents=("current_result",),
            reference_date="2026-08-02",
            required_claims=("score",),
            freshness_required=True,
        ),
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(sports),
    )

    assert result.status is GateStatus.REPAIR
    assert "fact_check.exact_asset_not_unique" in {
        item.code for item in result.violations
    }


@pytest.mark.parametrize("asset_count", (0, 2))
def test_unscoped_evidence_plan_without_unique_exact_asset_repairs(
    asset_count: int,
) -> None:
    assets = tuple(
        _asset(
            f"sports-{index}",
            asset_type="recipe",
            domains=("sports",),
            intents=("current_result",),
        )
        for index in range(asset_count)
    )
    plan = replace(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="bounded sports query",
            intents=("current_result",),
        ),
        domains=("sports",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="sports",
            entities=(),
            search_query="bounded sports query",
            intents=("current_result",),
            reference_date="2026-08-02",
            required_claims=("score",),
            freshness_required=True,
        ),
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(*assets),
    )

    assert result.status is GateStatus.REPAIR
    assert result.effective_plan is None
    assert "fact_check.exact_asset_not_unique" in {
        violation.code for violation in result.violations
    }


def test_current_result_cannot_disable_freshness_at_plan_gate() -> None:
    asset = _asset("scores")
    plan = _plan(
        mode=ExecutionMode.DIRECT_ANSWER,
        primary_asset=AssetRef("skill", "scores"),
        fact_required=True,
        owner=EvidenceOwner.ASSET,
        intents=("current_result",),
        freshness_required=False,
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(asset),
    )

    assert result.status is GateStatus.REPAIR
    assert "fact_check.freshness_required" in {
        item.code for item in result.violations
    }


def test_freshness_sensitive_asset_cannot_disable_freshness_at_plan_gate() -> None:
    asset = _asset("live-catalog", freshness_sensitive=True)
    plan = _plan(
        mode=ExecutionMode.DIRECT_ANSWER,
        primary_asset=AssetRef("skill", "live-catalog"),
        fact_required=True,
        owner=EvidenceOwner.ASSET,
        intents=("definition",),
        freshness_required=False,
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(asset),
    )

    assert result.status is GateStatus.REPAIR
    assert "fact_check.freshness_required" in {
        item.code for item in result.violations
    }


def test_definition_is_not_forced_into_freshness_policy() -> None:
    asset = _asset("dictionary")
    plan = _plan(
        mode=ExecutionMode.DIRECT_ANSWER,
        primary_asset=AssetRef("skill", "dictionary"),
        fact_required=True,
        owner=EvidenceOwner.ASSET,
        intents=("definition",),
        freshness_required=False,
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(asset),
    )

    assert result.status is GateStatus.PASS


@pytest.mark.parametrize(
    ("mode", "allowed_tools", "code"),
    [
        (
            ExecutionMode.DIRECT_ANSWER,
            ("web_search",),
            "fact_check.evidence_capable_mode_required",
        ),
        (
            ExecutionMode.ANSWER_WITH_EVIDENCE,
            (),
            "fact_check.collector_required",
        ),
        (
            ExecutionMode.ANSWER_WITH_EVIDENCE,
            ("web_fetch",),
            "fact_check.collector_required",
        ),
    ],
)
def test_required_fact_check_reverse_invariant_fails_closed(
    mode: ExecutionMode,
    allowed_tools: tuple[str, ...],
    code: str,
) -> None:
    assets = tuple(
        _asset(name, asset_type="native_tool")
        for name in allowed_tools
    )
    result = PlanGate().evaluate(
        _plan(
            mode=mode,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="낯선 작품 등장인물",
            allowed_tools=allowed_tools,
        ),
        candidates=_candidates(),
        catalog=_catalog(*assets),
    )

    assert result.status is GateStatus.REPAIR
    assert code in {item.code for item in result.violations}


def test_required_fact_check_accepts_approved_non_web_collector() -> None:
    file_read = _asset("file_read", asset_type="native_tool")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="workspace drama catalog",
            allowed_tools=("file_read",),
        ),
        candidates=_candidates(),
        catalog=_catalog(file_read),
    )

    assert result.status is GateStatus.PASS


def test_required_fact_check_rejects_side_effecting_non_web_collector() -> None:
    unsafe_file_read = _asset(
        "file_read",
        asset_type="native_tool",
        read_only=False,
        side_effects=True,
    )
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="workspace drama catalog",
            allowed_tools=("file_read",),
        ),
        candidates=_candidates(),
        catalog=_catalog(unsafe_file_read),
    )

    assert result.status is GateStatus.REPAIR
    assert "fact_check.collector_required" in {
        item.code for item in result.violations
    }


def test_required_fact_check_accepts_selected_read_only_skill_collector() -> None:
    execute_skill = _asset("execute_skill", asset_type="native_tool")
    calendar_skill = _asset("google-calendar-skill", asset_type="skill")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="calendar events today",
            allowed_assets=(AssetRef("skill", "google-calendar-skill"),),
            allowed_tools=("execute_skill",),
        ),
        candidates=_candidates(),
        catalog=_catalog(execute_skill, calendar_skill),
    )

    assert result.status is GateStatus.PASS


def test_required_fact_check_rejects_unbound_skill_adapter() -> None:
    execute_skill = _asset("execute_skill", asset_type="native_tool")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact_required=True,
            owner=EvidenceOwner.PLANNER,
            search_query="calendar events today",
            allowed_tools=("execute_skill",),
        ),
        candidates=_candidates(),
        catalog=_catalog(execute_skill),
    )

    assert result.status is GateStatus.REPAIR
    assert "fact_check.collector_required" in {
        item.code for item in result.violations
    }


def test_unknown_asset_and_recipe_type_mismatch_request_repair() -> None:
    unknown = AssetRef("skill", "missing")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=unknown,
            allowed_assets=(unknown,),
            owner=EvidenceOwner.NONE,
        ),
        candidates=_candidates(),
        catalog=_catalog(_asset("missing", asset_type="recipe")),
    )

    assert result.status is GateStatus.REPAIR
    assert "asset.unknown" in {item.code for item in result.violations}


def test_safe_declared_direct_asset_passes() -> None:
    ref = AssetRef("skill", "weather")
    plan = _plan(
        mode=ExecutionMode.DIRECT_ANSWER,
        primary_asset=ref,
        allowed_assets=(ref,),
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(_asset()),
    )

    assert result.status is GateStatus.PASS
    assert result.effective_plan is not None
    assert result.effective_plan.approved_asset_fingerprint == "d" * 64


def test_side_effecting_direct_asset_requires_confirmation() -> None:
    ref = AssetRef("skill", "writer")
    plan = _plan(
        mode=ExecutionMode.DIRECT_ANSWER,
        primary_asset=ref,
        allowed_assets=(ref,),
        requires_confirmation=True,
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(
            _asset(
                "writer",
                read_only=False,
                side_effects=True,
                requires_confirmation=True,
            )
        ),
    )

    assert result.status is GateStatus.CONFIRMATION_REQUIRED
    assert [item.code for item in result.violations] == [
        "asset.confirmation_required"
    ]
    assert result.effective_plan is not None
    assert result.effective_plan.approved_asset_fingerprint == "d" * 64


def test_reminder_mutation_stops_for_confirmation_before_dispatch() -> None:
    ref = AssetRef("native_tool", "cron")
    plan = _plan(
        mode=ExecutionMode.DIRECT_ANSWER,
        primary_asset=ref,
        allowed_assets=(ref,),
        clarification_required=True,
        requires_confirmation=True,
    )

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(
            _asset(
                "cron",
                asset_type="native_tool",
                read_only=False,
                side_effects=True,
                requires_confirmation=True,
            )
        ),
    )

    assert result.status is GateStatus.CONFIRMATION_REQUIRED
    assert result.effective_plan is not None
    assert result.effective_plan.approved_asset_fingerprint == "d" * 64


def test_missing_side_effect_confirmation_flag_requests_repair() -> None:
    ref = AssetRef("skill", "writer")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=ref,
            allowed_assets=(ref,),
            requires_confirmation=False,
        ),
        candidates=_candidates(),
        catalog=_catalog(
            _asset("writer", read_only=False, side_effects=True)
        ),
    )

    assert result.status is GateStatus.REPAIR
    assert [item.code for item in result.violations] == [
        "asset.confirmation_flag_required"
    ]


def test_undeclared_direct_asset_is_rejected() -> None:
    ref = AssetRef("skill", "legacy")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=ref,
            allowed_assets=(ref,),
        ),
        candidates=_candidates(),
        catalog=_catalog(_asset("legacy", declared=False)),
    )

    assert result.status is GateStatus.REJECT
    assert [item.code for item in result.violations] == [
        "asset.undeclared_direct_execution"
    ]


def test_catalog_drift_requests_repair() -> None:
    plan = replace(_plan(), catalog_fingerprint="old")

    result = PlanGate().evaluate(
        plan,
        candidates=_candidates(),
        catalog=_catalog(),
    )

    assert result.status is GateStatus.REPAIR
    assert result.violations[0].code == "catalog.fingerprint_mismatch"


def test_direct_answer_without_capability_passes() -> None:
    result = PlanGate().evaluate(
        _plan(mode=ExecutionMode.DIRECT_ANSWER),
        candidates=_candidates(),
        catalog=_catalog(),
    )

    assert result.status is GateStatus.PASS


def test_execute_skill_meta_tool_requires_allowed_skill() -> None:
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.DIRECT_ANSWER,
            allowed_tools=("execute_skill",),
        ),
        candidates=_candidates(),
        catalog=_catalog(_asset()),
    )

    assert result.status is GateStatus.REPAIR
    assert result.violations[0].code == (
        "asset.execute_skill_without_allowed_skill"
    )


def test_execute_skill_meta_tool_accepts_catalogued_allowed_skill() -> None:
    ref = AssetRef("skill", "weather")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.DIRECT_ANSWER,
            allowed_assets=(ref,),
            allowed_tools=("execute_skill",),
        ),
        candidates=_candidates(),
        catalog=_catalog(_asset()),
    )

    assert result.status is GateStatus.PASS


def test_full_coverage_recipe_with_top_level_tool_requests_repair() -> None:
    recipe = AssetRef("recipe", "sports-live")
    result = PlanGate().evaluate(
        _plan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            primary_asset=recipe,
            allowed_tools=("web_search",),
        ),
        candidates=_candidates(),
        catalog=_catalog(
            _asset("sports-live", asset_type="recipe"),
            _asset("web_search", asset_type="native_tool"),
        ),
    )

    assert result.status is GateStatus.REPAIR
    assert "asset.full_coverage_recipe_has_top_level_tools" in {
        violation.code for violation in result.violations
    }
