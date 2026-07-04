from simpleclaw.agent.fact_plan import build_fact_plan
from simpleclaw.agent.response_router import classify_response_route


def test_scenario_plan_contains_required_structural_slots():
    question = "한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘"
    decision = classify_response_route(question)

    plan = build_fact_plan(question, decision, max_iterations=4)

    names = [slot.name for slot in plan.slots]
    assert "current_state" in names
    assert "decision_rules" in names
    assert "remaining_variables" in names
    assert "comparison_set" in names
    assert "calculation_inputs" in names
    assert plan.requires_calculation is True
    assert plan.max_iterations == 4


def test_policy_plan_contains_rules_and_user_condition_slots():
    question = "이 정책이 내 상황에 적용되는지 조건별로 판단해줘"
    decision = classify_response_route(question)

    plan = build_fact_plan(question, decision, max_iterations=3)

    names = [slot.name for slot in plan.slots]
    assert "decision_rules" in names
    assert "subject_conditions" in names
    assert "calculation_inputs" not in names
