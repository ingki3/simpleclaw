"""BIZ-491 — UnifiedTurnPlan 모델·schema·semantic clamp 계약 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simpleclaw.agent.response_router import ResponseRoute
from simpleclaw.agent.turn_plan import (
    UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
    ContextRelation,
    EvidenceOwner,
    ExecutionMode,
    parse_turn_plan_data,
    parse_turn_plan_payload,
)
from simpleclaw.evaluation.turn_planner_eval import load_fixtures, score_prediction


def _payload(**overrides: object) -> dict[str, object]:
    """structured provider가 반환할 완전한 기본 payload를 만든다."""
    payload: dict[str, object] = {
        "context": {
            "relation": "standalone",
            "use_prior_context": False,
            "selected_turn_ids": [],
            "standalone_question": "CAP 정리를 설명해줘",
            "unresolved_references": [],
            "ignored_context_reason": "",
        },
        "clarification": {
            "required": False,
            "question": "",
            "options": [],
            "reason": "",
        },
        "domains": ["technology"],
        "intents": ["explanation"],
        "fact_check": {
            "required": False,
            "owner": "none",
            "domain": "none",
            "entities": [],
            "search_query": "",
            "required_claims": [],
            "freshness_required": False,
            "reason": "",
        },
        "execution": {
            "mode": "direct_answer",
            "primary_asset": {
                "asset_type": "none",
                "asset_name": "__none__",
            },
            "allowed_assets": [],
            "allowed_tools": [],
            "requires_confirmation": False,
            "reason": "정적 설명",
        },
        "confidence": 0.95,
        "decision_summary": "과거 문맥 없이 직접 설명한다.",
    }
    payload.update(overrides)
    return payload


def _assert_strict_objects(schema: dict[str, object]) -> None:
    """중첩 object까지 strict required/additionalProperties 계약을 확인한다."""
    if schema.get("type") == "object":
        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(properties)
        assert schema["propertyOrdering"] == list(properties)
        for child in properties.values():
            assert isinstance(child, dict)
            _assert_strict_objects(child)
    if schema.get("type") == "array":
        items = schema["items"]
        assert isinstance(items, dict)
        _assert_strict_objects(items)


def test_enum_values_are_stable() -> None:
    """외부 schema와 downstream 분기가 사용하는 enum 문자열을 고정한다."""
    assert [item.value for item in ContextRelation] == [
        "standalone",
        "same_thread",
        "related_reference",
        "topic_shift",
        "unclear",
    ]
    assert [item.value for item in ExecutionMode] == [
        "clarify",
        "direct_answer",
        "execute_asset",
        "tool_loop",
        "fact_check",
        "complex_fact",
        "recipe",
    ]
    assert [item.value for item in EvidenceOwner] == ["none", "planner", "asset"]


def test_schema_is_gemini_compatible_and_strict() -> None:
    """nullable 없이 no-asset sentinel을 쓰고 모든 object를 strict하게 만든다."""
    _assert_strict_objects(UNIFIED_TURN_PLAN_RESPONSE_SCHEMA)
    encoded = json.dumps(UNIFIED_TURN_PLAN_RESPONSE_SCHEMA)
    assert '"null"' not in encoded
    assert "route" not in UNIFIED_TURN_PLAN_RESPONSE_SCHEMA["properties"]
    primary = UNIFIED_TURN_PLAN_RESPONSE_SCHEMA["properties"]["execution"][
        "properties"
    ]["primary_asset"]
    assert primary["properties"]["asset_type"]["enum"][0] == "none"
    assert primary["properties"]["asset_name"]["type"] == "string"


def test_parser_converts_no_asset_sentinel_to_none() -> None:
    """provider용 sentinel은 Python 모델에서 실제 None으로 변환한다."""
    plan = parse_turn_plan_payload(
        json.dumps(_payload(), ensure_ascii=False),
        original_text="CAP 정리를 설명해줘",
        catalog_fingerprint="catalog-v1",
    )

    assert plan.execution.primary_asset is None
    assert plan.catalog_fingerprint == "catalog-v1"
    assert plan.source == "llm"


def test_parser_preserves_selected_context_and_fact_plan() -> None:
    """SK follow-up의 선택 ID·standalone 질문·fact owner를 손실 없이 보존한다."""
    payload = _payload(
        context={
            "relation": "same_thread",
            "use_prior_context": True,
            "selected_turn_ids": ["m101"],
            "standalone_question": "SK와 NVIDIA의 오늘 협업 발표를 확인해 정리해줘",
            "unresolved_references": [],
            "ignored_context_reason": "",
        },
        domains=["news"],
        intents=["news", "realtime_lookup"],
        fact_check={
            "required": True,
            "owner": "planner",
            "domain": "news",
            "entities": ["SK", "NVIDIA"],
            "search_query": "SK NVIDIA 오늘 협업 발표",
            "required_claims": ["오늘 발표 내용", "양사 역할"],
            "freshness_required": True,
            "reason": "현재 발표 확인 필요",
        },
        execution={
            "mode": "fact_check",
            "primary_asset": {
                "asset_type": "skill",
                "asset_name": "realtime-lookup-skill",
            },
            "allowed_assets": [
                {
                    "asset_type": "skill",
                    "asset_name": "realtime-lookup-skill",
                }
            ],
            "allowed_tools": ["execute_skill"],
            "requires_confirmation": False,
            "reason": "최신 발표 조회",
        },
    )

    plan = parse_turn_plan_data(
        payload,
        original_text="오늘 있었던 발표야. 체크해봐",
    )

    assert plan.context.relation is ContextRelation.SAME_THREAD
    assert plan.context.use_prior_context is True
    assert plan.context.selected_turn_ids == ("m101",)
    assert plan.fact_check.owner is EvidenceOwner.PLANNER
    assert plan.fact_check.required_claims == ("오늘 발표 내용", "양사 역할")
    assert plan.execution.mode is ExecutionMode.FACT_CHECK
    assert plan.execution.primary_asset is not None
    assert plan.execution.primary_asset.name == "realtime-lookup-skill"


def test_topic_shift_discards_selected_history() -> None:
    """topic shift는 모델이 ID를 잘못 반환해도 과거 문맥을 downstream에 넘기지 않는다."""
    payload = _payload(
        context={
            "relation": "topic_shift",
            "use_prior_context": True,
            "selected_turn_ids": ["m401", "m402"],
            "standalone_question": "내일 서울 날씨를 알려줘",
            "unresolved_references": [],
            "ignored_context_reason": "새 날씨 질문",
        },
        execution={
            "mode": "fact_check",
            "primary_asset": {
                "asset_type": "skill",
                "asset_name": "realtime-lookup-skill",
            },
            "allowed_assets": [],
            "allowed_tools": [],
            "requires_confirmation": False,
            "reason": "날씨 조회",
        },
    )

    plan = parse_turn_plan_data(payload, original_text="내일 서울 날씨는 어때?")

    assert plan.context.relation is ContextRelation.TOPIC_SHIFT
    assert plan.context.use_prior_context is False
    assert plan.context.selected_turn_ids == ()


def test_unclear_reference_forces_clarify_mode() -> None:
    """미해결 지시 대상은 다른 실행 mode를 반환해도 clarify로 fail-closed한다."""
    payload = _payload(
        context={
            "relation": "unclear",
            "use_prior_context": True,
            "selected_turn_ids": ["m501"],
            "standalone_question": "정리할 대상을 확인해줘",
            "unresolved_references": ["이거"],
            "ignored_context_reason": "",
        },
        clarification={
            "required": False,
            "question": "",
            "options": [],
            "reason": "",
        },
        execution={
            "mode": "execute_asset",
            "primary_asset": {
                "asset_type": "skill",
                "asset_name": "summarize",
            },
            "allowed_assets": [],
            "allowed_tools": [],
            "requires_confirmation": False,
            "reason": "잘못된 직접 실행",
        },
    )

    plan = parse_turn_plan_data(payload, original_text="이거 좀 정리해줘")

    assert plan.context.selected_turn_ids == ()
    assert plan.clarification.required is True
    assert plan.execution.mode is ExecutionMode.CLARIFY
    assert plan.execution.primary_asset is None


@pytest.mark.parametrize(
    ("mode", "expected_route"),
    [
        ("direct_answer", ResponseRoute.STANDARD_TOOL_LOOP),
        ("tool_loop", ResponseRoute.STANDARD_TOOL_LOOP),
        ("fact_check", ResponseRoute.CURRENT_FACT_GUARDED_LOOP),
        ("complex_fact", ResponseRoute.COMPLEX_FACT_WORKFLOW),
    ],
)
def test_route_decision_adapter_uses_execution_mode_as_source_of_truth(
    mode: str,
    expected_route: ResponseRoute,
) -> None:
    """legacy RouteDecision은 별도 route 필드 없이 execution.mode에서만 파생한다."""
    payload = _payload(
        execution={
            "mode": mode,
            "primary_asset": {
                "asset_type": "none",
                "asset_name": "__none__",
            },
            "allowed_assets": [],
            "allowed_tools": [],
            "requires_confirmation": False,
            "reason": "adapter test",
        }
    )
    plan = parse_turn_plan_data(payload, original_text="질문")

    assert plan.to_route_decision().route is expected_route


def test_fixed_fixture_predictions_round_trip_at_one_hundred_percent() -> None:
    """BIZ-488 fixed fixture의 critical prediction을 모델 adapter로 손실 없이 평가한다."""
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "unified_turn_planner_cases.jsonl"
    )
    fixtures = load_fixtures(fixture_path)
    critical = [fixture for fixture in fixtures if fixture.critical]
    assert critical

    results = []
    for fixture in critical:
        plan = parse_turn_plan_data(
            fixture.prediction,
            original_text=fixture.current,
        )
        results.append(score_prediction(fixture, plan.to_evaluator_payload()))

    assert all(result.passed for result in results)
    assert sum(result.passed for result in results) / len(results) == 1.0


def test_assistant_false_claim_is_not_selected_as_evidence_context() -> None:
    """fixed SK follow-up에서 과거 assistant 오답 ID가 선택되지 않는 계약을 고정한다."""
    fixture_path = (
        Path(__file__).parents[1] / "fixtures" / "unified_turn_planner_cases.jsonl"
    )
    fixture = next(
        item
        for item in load_fixtures(fixture_path)
        if item.id == "sk-nvidia-followup"
    )

    plan = parse_turn_plan_data(
        fixture.prediction,
        original_text=fixture.current,
    )

    assert plan.context.selected_turn_ids == ("m101",)
    assert "m102" not in plan.context.selected_turn_ids
