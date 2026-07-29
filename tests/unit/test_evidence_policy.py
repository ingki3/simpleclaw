"""BIZ-520 — current-turn evidence outcome contract tests."""

from __future__ import annotations

from simpleclaw.agent.evidence_policy import (
    EvidenceFreshness,
    EvidenceRequirement,
    EvidenceSourceType,
    EvidenceStatus,
    assess_realtime_result,
    assess_tool_result,
    requirement_from_turn_analysis,
    requirement_from_turn_plan,
)
from simpleclaw.agent.response_router import ResponseRoute
from simpleclaw.agent.turn_analysis import TurnAnalysis
from simpleclaw.agent.turn_plan import (
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)


def _fact_plan(*, required: bool = True) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="작품 정보를 확인해줘",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question='"이런 엿같은 사랑" 등장인물을 확인해줘',
        ),
        clarification=ClarificationPlan(required=False),
        domains=("entertainment",),
        intents=("drama_info",),
        fact_check=FactCheckPlan(
            required=required,
            owner=EvidenceOwner.PLANNER if required else EvidenceOwner.NONE,
            domain="entertainment" if required else "none",
            entities=("이런 엿같은 사랑",),
            search_query='"이런 엿같은 사랑" Netflix 등장인물',
            freshness_required=False,
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.FACT_CHECK if required else ExecutionMode.DIRECT_ANSWER,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=("web_search",) if required else (),
            requires_confirmation=False,
            reason="test",
        ),
        confidence=0.9,
        decision_summary="test",
    )


def test_legacy_and_unified_adapters_share_required_contract() -> None:
    legacy = TurnAnalysis(
        original_text="등장인물 찾아줘",
        normalized_question='"이런 엿같은 사랑" 등장인물 찾아줘',
        domains=("entertainment",),
        intents=("drama_info",),
        route=ResponseRoute.STANDARD_TOOL_LOOP,
        evidence_required=True,
    )

    legacy_requirement = requirement_from_turn_analysis(legacy)
    unified_requirement = requirement_from_turn_plan(_fact_plan())

    assert legacy_requirement.required is True
    assert unified_requirement.required is True
    assert legacy_requirement.query == legacy.normalized_question
    assert unified_requirement.query == '"이런 엿같은 사랑" Netflix 등장인물'
    assert unified_requirement.allowed_collectors == frozenset({"web_search"})


def test_required_false_stays_not_searched_without_forcing_collection() -> None:
    requirement = requirement_from_turn_plan(_fact_plan(required=False))

    assert requirement.required is False
    assert requirement.initial_state().status is EvidenceStatus.NOT_SEARCHED
    assert requirement.initial_state().attempted is False


def test_structured_realtime_found_is_fresh_and_usable() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query="KBO 오늘 경기",
        domain="sports",
        allowed_collectors=frozenset({"web_search"}),
        freshness_required=True,
    )
    state = assess_realtime_result(
        requirement,
        {
            "lookup_status": "found",
            "confidence": "high",
            "facts": [{"type": "sports_score", "source_url": "https://sports.example"}],
        },
        usable=True,
        as_of="2026-07-29T23:00:00+09:00",
    )

    assert state.status is EvidenceStatus.FOUND
    assert state.source_type is EvidenceSourceType.STRUCTURED_REALTIME
    assert state.freshness is EvidenceFreshness.CURRENT_TURN
    assert state.usable is True


def test_explicit_empty_and_provider_failure_are_not_coerced() -> None:
    requirement = EvidenceRequirement(required=True, query="경기", domain="sports")

    not_found = assess_realtime_result(
        requirement,
        {"lookup_status": "not_found", "confidence": "high", "facts": []},
        usable=False,
    )
    failed = assess_realtime_result(
        requirement,
        {"lookup_status": "failed", "confidence": "low", "facts": []},
        usable=False,
        failure_reason="provider timeout",
    )

    assert not_found.status is EvidenceStatus.NOT_FOUND
    assert failed.status is EvidenceStatus.FAILED
    assert failed.failure_reason == "provider timeout"


def test_tool_success_without_valid_source_is_unusable() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query="낯선 작품 등장인물",
        domain="entertainment",
        allowed_collectors=frozenset({"web_search"}),
    )

    state = assess_tool_result(
        requirement,
        tool_name="web_search",
        output="검색은 성공했다는 자연어뿐이고 출처 URL은 없습니다.",
    )

    assert state.attempted is True
    assert state.status is EvidenceStatus.UNUSABLE
    assert state.usable is False


def test_web_empty_error_and_valid_result_have_distinct_states() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query='"이런 엿같은 사랑" 등장인물',
        domain="entertainment",
        allowed_collectors=frozenset({"web_search"}),
    )

    empty = assess_tool_result(
        requirement,
        tool_name="web_search",
        output="WEB_SEARCH_RESULTS: query (0 results)",
    )
    failed = assess_tool_result(
        requirement,
        tool_name="web_search",
        output="Error: web_search failed — timeout",
    )
    found = assess_tool_result(
        requirement,
        tool_name="web_search",
        output=(
            "WEB_SEARCH_RESULTS: query (1 results)\n"
            "1. Netflix cast\n"
            "URL: https://www.netflix.com/example"
        ),
    )

    assert empty.status is EvidenceStatus.NOT_FOUND
    assert failed.status is EvidenceStatus.FAILED
    assert found.status is EvidenceStatus.FOUND
    assert found.usable is True


def test_not_found_phrase_inside_sourced_result_is_not_explicit_empty() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query="metadata correction",
        domain="entertainment",
        allowed_collectors=frozenset({"web_search"}),
    )

    state = assess_tool_result(
        requirement,
        tool_name="web_search",
        output=(
            "WEB_SEARCH_RESULTS: query (1 results)\n"
            "1. Why the old title was not found\n"
            "URL: https://example.com/correction"
        ),
    )

    assert state.status is EvidenceStatus.FOUND
