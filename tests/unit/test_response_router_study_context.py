"""BIZ-394 — 구조적 영향 라우팅 + study context freshness/confidence gate 회귀 테스트.

라우터가 특정 사건 키워드(예: "월드컵")가 아니라 "현재성/도메인 이벤트 + 분석/영향"
구조적 cue 조합으로 현재성·영향 분석 질문을 잡는지, 그리고 답변 근거로 주입되는
Agent Study context 가 stale/저신뢰면 현재 사실 재조회(at-least guarded)를 강제하는지
검증한다.
"""

from __future__ import annotations

import pytest

from simpleclaw.agent.response_router import (
    ResponseRoute,
    StudyContextAssessment,
    assess_study_context,
    classify_response_route,
)


# --------------------------------------------------------------------------
# Structural impact/current routing (특정 사건 키워드 비의존)
# --------------------------------------------------------------------------


def test_market_impact_question_routes_to_complex_or_current_guarded():
    decision = classify_response_route(
        "OpenAI 상장 연기가 증시에 끼치는 영향을 조사해줘",
        route_threshold=3,
    )

    assert decision.route in {
        ResponseRoute.CURRENT_FACT_GUARDED_LOOP,
        ResponseRoute.COMPLEX_FACT_WORKFLOW,
    }
    assert decision.needs_current_facts is True


def test_market_impact_question_does_not_fall_to_standard_default():
    decision = classify_response_route(
        "OpenAI 상장 연기가 증시에 끼치는 영향을 조사해줘",
        route_threshold=3,
    )
    assert decision.route != ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.needs_impact_analysis is True


def test_world_cup_scenario_routes_to_complex_fact_workflow():
    decision = classify_response_route(
        "대한민국 월드컵 32강 진출 가능성이 어떻게 되지?",
        route_threshold=3,
    )

    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert decision.needs_current_facts is True
    assert decision.needs_calculation is True or decision.needs_remaining_variables is True


def test_impact_cue_alone_is_not_complex():
    """분석/영향 cue 단독(도메인 이벤트/현재성 없음)으로는 가중하지 않는다."""
    decision = classify_response_route("이 코드 구조의 배경을 설명해줘", route_threshold=3)
    assert decision.route == ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.needs_impact_analysis is False


def test_domain_event_definition_question_stays_standard():
    """도메인 이벤트 단어가 있어도 분석/영향 cue 없이 정의성 질문이면 standard."""
    decision = classify_response_route("IPO가 뭐야?", route_threshold=3)
    assert decision.route == ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.needs_impact_analysis is False


# --------------------------------------------------------------------------
# Study context freshness/confidence gate
# --------------------------------------------------------------------------

_STALE_STUDY_BLOCK = (
    "## Agent Study Context\n"
    "- Topic: OpenAI 상장\n"
    "  Confidence: 0.40\n"
    "## Agent Study Context — Freshness Warning\n"
    "Freshness: stale\n"
    "경고: 위 배경지식은 신뢰 근거로 쓰기에 한계가 있습니다.\n"
)

_FRESH_STUDY_BLOCK = (
    "## Agent Study Context\n"
    "- Topic: OpenAI 상장\n"
    "  Confidence: 0.92\n"
)


def test_assess_study_context_detects_stale_and_low_confidence():
    assessment = assess_study_context(_STALE_STUDY_BLOCK)
    assert isinstance(assessment, StudyContextAssessment)
    assert assessment.has_context is True
    assert assessment.is_stale is True
    assert assessment.is_low_confidence is True
    assert assessment.is_unreliable is True


def test_assess_fresh_study_context_is_reliable():
    assessment = assess_study_context(_FRESH_STUDY_BLOCK)
    assert assessment.has_context is True
    assert assessment.is_stale is False
    assert assessment.is_low_confidence is False
    assert assessment.is_unreliable is False


def test_assess_empty_study_context_has_no_context():
    assessment = assess_study_context("")
    assert assessment.has_context is False
    assert assessment.is_unreliable is False


def test_stale_study_context_forces_current_lookup_on_currentness_question():
    decision = classify_response_route(
        "OpenAI 지금 상황 어때?",
        route_threshold=3,
        study_context=_STALE_STUDY_BLOCK,
    )
    assert decision.route != ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.needs_current_facts is True
    assert decision.study_context_unreliable is True
    assert any("study_context" in reason for reason in decision.reasons)


def test_fresh_study_context_does_not_force_lookup():
    decision = classify_response_route(
        "OpenAI 지금 상황 어때?",
        route_threshold=3,
        study_context=_FRESH_STUDY_BLOCK,
    )
    assert decision.study_context_unreliable is False


def test_stale_study_context_does_not_escalate_non_current_question():
    """stale study 가 있어도 현재성/영향/시나리오성이 아닌 질문은 escalate 하지 않는다."""
    decision = classify_response_route(
        "파이썬 데코레이터 문법 설명해줘",
        route_threshold=3,
        study_context=_STALE_STUDY_BLOCK,
    )
    assert decision.route == ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.study_context_unreliable is False


@pytest.mark.parametrize(
    "query",
    [
        "OpenAI 실적 전망이 어때?",
        "엔비디아 가이던스가 시장에 주는 파급은?",
        "이번 인수 합병이 주가에 미칠 리스크 분석해줘",
    ],
)
def test_structural_domain_event_plus_analysis_marks_impact(query):
    decision = classify_response_route(query, route_threshold=3)
    assert decision.needs_impact_analysis is True
    assert decision.needs_current_facts is True
    assert decision.route != ResponseRoute.STANDARD_TOOL_LOOP
