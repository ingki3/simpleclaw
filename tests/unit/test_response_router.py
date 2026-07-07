"""keyword route classifier 계약 테스트.

BIZ-426 — classify_response_route 는 primary 가 아니라 LLM turn analysis
비활성/실패 시의 결정적 fallback 이다. 이 테스트들은 그 fallback 계약을 지킨다.
"""

from simpleclaw.agent.response_router import ResponseRoute, classify_response_route


def test_smalltalk_uses_standard_loop_not_complex():
    decision = classify_response_route("안녕?")
    assert decision.route == ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.complexity_score == 0
    assert "smalltalk" in decision.reasons


def test_simple_explanation_uses_standard_loop():
    decision = classify_response_route("파이썬 list comprehension 설명해줘")
    assert decision.route == ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.complexity_score <= 1


def test_single_current_fact_uses_guarded_loop():
    decision = classify_response_route("오늘 서울 날씨 어때?")
    assert decision.route == ResponseRoute.CURRENT_FACT_GUARDED_LOOP
    assert decision.needs_current_facts is True
    assert decision.needs_scenario_reasoning is False if hasattr(decision, "needs_scenario_reasoning") else True


def test_stock_current_quote_is_guarded_not_complex():
    decision = classify_response_route("지금 테슬라 주가 얼마야?")
    assert decision.route == ResponseRoute.CURRENT_FACT_GUARDED_LOOP
    assert decision.needs_current_facts is True
    assert decision.needs_rules is False


def test_scenario_question_uses_complex_workflow():
    decision = classify_response_route("한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘")
    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert decision.needs_current_facts is True
    assert decision.needs_rules is True
    assert decision.needs_remaining_variables is True
    assert decision.needs_calculation is True


def test_policy_applicability_uses_complex_workflow():
    decision = classify_response_route("이 정책이 내 상황에 적용되는지 조건별로 판단해줘")
    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert decision.needs_rules is True
    assert decision.needs_comparison_or_conditions is True


def test_route_threshold_can_promote_medium_complexity_question():
    decision = classify_response_route(
        "이 정책 적용 조건을 비교해줘",
        route_threshold=2,
    )
    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW


# ----------------------------------------------------------------------
# BIZ-425 — 정규화된 질문 기준 route 동작 보존
# ----------------------------------------------------------------------


def test_remaining_games_scenario_still_uses_complex_workflow():
    """경우의 수/남은 변수 질문은 정규화/capability 도입 후에도 complex 유지."""
    decision = classify_response_route(
        "롯데가 남은 경기에서 몇 승을 해야 3위 가능성이 있어? 경우의 수 알려줘"
    )
    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert decision.needs_remaining_variables is True
    assert decision.needs_calculation is True


def test_normalized_followup_lookup_is_guarded_not_complex():
    """맥락 접두어가 붙은 정규화 조회 질문은 complex 로 과승격되지 않는다."""
    decision = classify_response_route(
        "(직전 대화 맥락: 롯데, 야구, KT) 그럼 현재 순위는?"
    )
    assert decision.route == ResponseRoute.CURRENT_FACT_GUARDED_LOOP
    assert decision.needs_current_facts is True


def test_normalized_prefix_does_not_add_rule_or_condition_cues():
    """정규화 접두 문구 자체는 rule/condition cue 점수를 만들지 않는다."""
    plain = classify_response_route("그럼 현재 순위는?")
    normalized = classify_response_route("(직전 대화 맥락: 부산, 출장) 그럼 현재 순위는?")
    assert normalized.route == plain.route
