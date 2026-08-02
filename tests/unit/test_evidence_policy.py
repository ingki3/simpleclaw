"""BIZ-520 — current-turn evidence outcome contract tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

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
            intents=("drama_info",) if required else (),
            freshness_required=False,
        ),
        execution=ExecutionPlan(
            mode=(
                ExecutionMode.ANSWER_WITH_EVIDENCE
                if required
                else ExecutionMode.DIRECT_ANSWER
            ),
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
        reference_date="2026-07-29",
        required_claims=("최종 점수", "승패"),
    )
    state = assess_realtime_result(
        requirement,
        {
            "lookup_status": "found",
            "kind": "sports",
            "confidence": "high",
            "facts": [
                {
                    "type": "sports_score",
                    "league": "KBO",
                    "event_date": "2026-07-29",
                    "away_team": "롯데",
                    "away_score": 5,
                    "home_team": "한화",
                    "home_score": 3,
                    "status": "final",
                    "winner": "롯데",
                    "source": "Naver Sports",
                    "source_url": "https://sports.example",
                }
            ],
            "timeline_validation": {"status": "final"},
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
        {
            "lookup_status": "not_found",
            "authoritative_empty": True,
            "confidence": "high",
            "facts": [],
        },
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


def test_not_found_without_authoritative_empty_evidence_is_unusable() -> None:
    requirement = EvidenceRequirement(required=True, query="경기", domain="sports")
    state = assess_realtime_result(
        requirement,
        {"lookup_status": "not_found", "confidence": "high", "facts": []},
        usable=False,
    )
    assert state.status is EvidenceStatus.UNUSABLE


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "[]",
        {"lookup_status": "found"},
        {"lookup_status": "found", "facts": "wrong"},
        {"lookup_status": "found", "facts": ["wrong"]},
        {"lookup_status": "found", "facts": [{"type": "sports_score"}]},
    ],
)
def test_malformed_structured_evidence_is_failed_with_schema_reason(
    payload: object,
) -> None:
    requirement = EvidenceRequirement(
        required=True,
        query="KBO 오늘 경기",
        domain="sports",
        freshness_required=True,
    )

    state = assess_realtime_result(
        requirement,
        payload,
        usable=False,
        as_of="2026-07-29T23:00:00+09:00",
    )

    assert state.status is EvidenceStatus.FAILED
    assert "schema failure" in state.failure_reason


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
            '1. "이런 엿같은 사랑" Netflix cast\n'
            "URL: https://www.netflix.com/example"
        ),
    )

    assert empty.status is EvidenceStatus.NOT_FOUND
    assert failed.status is EvidenceStatus.FAILED
    assert found.status is EvidenceStatus.FOUND
    assert found.usable is True


def test_explicit_stale_is_unusable_even_when_freshness_not_required() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query='"이런 엿같은 사랑" 등장인물',
        allowed_collectors=frozenset({"web_search"}),
        freshness_required=False,
    )

    state = assess_tool_result(
        requirement,
        tool_name="web_search",
        output=(
            'WEB_SEARCH_RESULTS: "이런 엿같은 사랑" 등장인물 (1 results)\n'
            "status: stale_or_pre_event\n"
            "URL: https://example.com/old"
        ),
    )

    assert state.status is EvidenceStatus.UNUSABLE
    assert state.freshness is EvidenceFreshness.STALE


def test_required_claim_must_be_covered_not_only_title() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query='"이런 엿같은 사랑" 등장인물',
        entities=("이런 엿같은 사랑",),
        required_claims=("하영",),
        allowed_collectors=frozenset({"web_search"}),
    )

    state = assess_tool_result(
        requirement,
        tool_name="web_search",
        output=(
            '"이런 엿같은 사랑" 등장인물은 정해영입니다.\n'
            "URL: https://example.com/cast"
        ),
    )

    assert state.status is EvidenceStatus.UNUSABLE
    assert "relevant" in state.failure_reason


def test_sourced_result_must_be_relevant_to_bounded_query() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query='"이런 엿같은 사랑" 등장인물',
        domain="entertainment",
        allowed_collectors=frozenset({"web_search"}),
    )

    state = assess_tool_result(
        requirement,
        tool_name="web_search",
        output=(
            "오늘 서울 날씨는 맑음\n"
            "URL: https://weather.example/seoul"
        ),
    )

    assert state.attempted is True
    assert state.status is EvidenceStatus.UNUSABLE
    assert state.usable is False
    assert "relevant" in state.failure_reason


@pytest.mark.parametrize("output", ["", "   "])
def test_untyped_empty_tool_output_is_failed_not_not_found(output: str) -> None:
    requirement = EvidenceRequirement(
        required=True,
        query='"이런 엿같은 사랑" 등장인물',
        domain="entertainment",
        allowed_collectors=frozenset({"web_search"}),
    )

    state = assess_tool_result(
        requirement,
        tool_name="web_search",
        output=output,
    )

    assert state.status is EvidenceStatus.FAILED
    assert state.status is not EvidenceStatus.NOT_FOUND
    assert "untyped empty" in state.failure_reason


def test_non_web_approved_collector_is_preserved_by_plan_adapter() -> None:
    plan = replace(
        _fact_plan(),
        execution=ExecutionPlan(
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=("file_read",),
            requires_confirmation=False,
            reason="read local evidence",
        ),
    )

    requirement = requirement_from_turn_plan(plan)
    state = assess_tool_result(
        requirement,
        tool_name="file_read",
        output='작품명: "이런 엿같은 사랑"\n등장인물: 하영',
    )

    assert requirement.allowed_collectors == frozenset({"file_read"})
    assert state.status is EvidenceStatus.FOUND
    assert state.source_type is EvidenceSourceType.APPROVED_TOOL


def test_typed_empty_from_approved_collector_is_not_found() -> None:
    requirement = EvidenceRequirement(
        required=True,
        query="calendar events today",
        domain="calendar",
        allowed_collectors=frozenset({"execute_skill"}),
    )

    state = assess_tool_result(
        requirement,
        tool_name="execute_skill",
        output='{"lookup_status":"not_found","events":[]}',
    )

    assert state.status is EvidenceStatus.NOT_FOUND
    assert state.attempted is True
    assert state.query == "calendar events today"
    assert state.source_type is EvidenceSourceType.APPROVED_TOOL


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
            "1. Metadata correction: why the old title was not found\n"
            "URL: https://example.com/correction"
        ),
    )

    assert state.status is EvidenceStatus.FOUND
