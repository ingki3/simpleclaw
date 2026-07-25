"""BIZ-426 — LLM 기반 turn analysis 파서/프롬프트/분석기 테스트."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.response_router import ResponseRoute
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.agent.turn_analysis import (
    TURN_ANALYSIS_RESPONSE_SCHEMA,
    analyze_turn_with_llm,
    parse_turn_analysis_payload,
)
from simpleclaw.llm.models import LLMResponse

# ----------------------------------------------------------------------
# parse_turn_analysis_payload
# ----------------------------------------------------------------------


def test_parse_valid_turn_analysis_payload():
    analysis = parse_turn_analysis_payload(
        """
        {
          "is_followup": true,
          "normalized_question": "직전 롯데-KT 경기 맥락에서 현재 KBO 순위를 알려줘",
          "context_summary": "직전 대화는 롯데-KT 경기 결과였다.",
          "confidence": 0.86,
          "needs_clarification": false,
          "ambiguity_options": [],
          "domains": ["sports"],
          "intents": ["standings", "realtime_lookup"],
          "route": "current_fact_guarded_loop",
          "complexity_score": 1,
          "needs_current_facts": true,
          "needs_rules": false,
          "needs_remaining_variables": false,
          "needs_calculation": false,
          "needs_comparison_or_conditions": false,
          "needs_conflict_resolution": false,
          "needs_impact_analysis": false,
          "reasons": ["follow-up resolved from recent sports context"]
        }
        """,
        original_text="그럼 현재 순위는?",
    )

    assert analysis.original_text == "그럼 현재 순위는?"
    assert "KBO" in analysis.normalized_question
    assert analysis.is_followup is True
    assert analysis.route == ResponseRoute.CURRENT_FACT_GUARDED_LOOP
    assert analysis.needs_current_facts is True
    assert analysis.domains == ("sports",)
    assert analysis.intents == ("standings", "realtime_lookup")
    assert analysis.needs_clarification is False
    assert analysis.source == "llm"


def test_parse_clamps_invalid_route_and_confidence():
    analysis = parse_turn_analysis_payload(
        '{"normalized_question":"hello","route":"unknown","confidence":2.0}',
        original_text="hello",
    )
    assert analysis.route == ResponseRoute.STANDARD_TOOL_LOOP
    assert analysis.confidence == 1.0


def test_parse_markdown_json_fence():
    analysis = parse_turn_analysis_payload(
        '```json\n{"normalized_question":"정리해줘","route":"standard_tool_loop"}\n```',
        original_text="정리해줘",
    )
    assert analysis.normalized_question == "정리해줘"


def test_parse_empty_normalized_question_falls_back_to_original():
    analysis = parse_turn_analysis_payload(
        '{"normalized_question":"  ","confidence":0.9}',
        original_text="원문 질문",
    )
    assert analysis.normalized_question == "원문 질문"


def test_parse_low_confidence_forces_clarification():
    analysis = parse_turn_analysis_payload(
        '{"normalized_question":"그거 확인","confidence":0.4,'
        '"ambiguity_options":["롯데 경기 결과","agent-study-daily 실패"]}',
        original_text="그거 확인해줘",
    )
    assert analysis.needs_clarification is True
    assert analysis.ambiguity_options == ["롯데 경기 결과", "agent-study-daily 실패"]


def test_parse_truncates_ambiguity_options_to_four():
    analysis = parse_turn_analysis_payload(
        '{"normalized_question":"x","needs_clarification":true,'
        '"ambiguity_options":["a","b","c","d","e","f"]}',
        original_text="x",
    )
    assert len(analysis.ambiguity_options) == 4


def test_parse_sanitizes_non_list_fields():
    analysis = parse_turn_analysis_payload(
        '{"normalized_question":"x","domains":"sports","intents":{"a":1},'
        '"reasons":null,"complexity_score":"99"}',
        original_text="x",
    )
    assert analysis.domains == ()
    assert analysis.intents == ()
    assert analysis.reasons == ()
    # complexity_score 는 0~10 으로 clamp 된다.
    assert analysis.complexity_score == 10


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError):
        parse_turn_analysis_payload("not json", original_text="x")


def test_non_object_json_raises_value_error():
    with pytest.raises(ValueError):
        parse_turn_analysis_payload('["not", "an", "object"]', original_text="x")


def test_to_route_decision_maps_all_slot_flags():
    analysis = parse_turn_analysis_payload(
        '{"normalized_question":"경우의 수 계산","route":"complex_fact_workflow",'
        '"complexity_score":5,"needs_current_facts":true,"needs_rules":true,'
        '"needs_remaining_variables":true,"needs_calculation":true,'
        '"needs_comparison_or_conditions":true,"needs_conflict_resolution":true,'
        '"needs_impact_analysis":true,"reasons":["scenario"],"confidence":0.9}',
        original_text="그럼 경우의 수는?",
    )
    decision = analysis.to_route_decision()
    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert decision.complexity_score == 5
    assert decision.reasons == ["scenario"]
    assert decision.needs_current_facts is True
    assert decision.needs_rules is True
    assert decision.needs_remaining_variables is True
    assert decision.needs_calculation is True
    assert decision.needs_comparison_or_conditions is True
    assert decision.needs_conflict_resolution is True
    assert decision.needs_impact_analysis is True


# ----------------------------------------------------------------------
# prompts/system/turn_analysis.yaml
# ----------------------------------------------------------------------


def test_turn_analysis_system_prompt_loads():
    spec = load_system_prompt("turn_analysis")
    # BIZ-427 — JSON shape 는 response_schema 가 강제하므로 프롬프트는
    # schema 준수 전제 + semantic guidance 만 담는다.
    assert "schema" in spec.system_prompt.lower()
    assert "normalized_question" in spec.system_prompt
    assert "complex_fact_workflow" in spec.system_prompt
    # 분석기는 사용자에게 답하지 않는다는 계약이 프롬프트에 명시돼야 한다.
    assert "Do not answer the user" in spec.system_prompt


# ----------------------------------------------------------------------
# analyze_turn_with_llm
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_turn_with_llm_sends_recent_context():
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(
            text=(
                '{"normalized_question":"롯데 맥락에서 현재 순위는?",'
                '"is_followup":true,"route":"current_fact_guarded_loop",'
                '"needs_current_facts":true,"domains":["sports"],'
                '"intents":["standings"],"confidence":0.9}'
            )
        )
    )

    analysis = await analyze_turn_with_llm(
        "그럼 현재 순위는?",
        recent_messages=[
            {"role": "user", "content": "오늘 롯데 야구 어떻게 되었지?"},
            {"role": "assistant", "content": "롯데가 KT에 패했습니다."},
        ],
        router=router,
        max_tokens=256,
    )

    request = router.send.call_args.args[0]
    assert request.route_name == "turn_analysis"
    assert request.backend_name is None
    assert request.max_tokens == 256
    assert "오늘 롯데 야구" in request.user_message
    assert "그럼 현재 순위는?" in request.user_message
    assert "Do not answer the user" in request.system_prompt
    assert analysis.normalized_question == "롯데 맥락에서 현재 순위는?"
    assert analysis.route == ResponseRoute.CURRENT_FACT_GUARDED_LOOP
    assert analysis.source == "llm"


@pytest.mark.asyncio
async def test_analyze_turn_with_llm_limits_recent_messages():
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text='{"normalized_question":"q"}')
    )

    await analyze_turn_with_llm(
        "질문",
        recent_messages=[
            {"role": "user", "content": f"message-{i}"} for i in range(10)
        ],
        router=router,
        max_recent_messages=3,
    )

    request = router.send.call_args.args[0]
    # 상한을 넘는 오래된 메시지는 프롬프트에 포함되지 않는다.
    assert "message-9" in request.user_message
    assert "message-7" in request.user_message
    assert "message-6" not in request.user_message


@pytest.mark.asyncio
async def test_analyze_turn_with_llm_falls_back_to_original_on_provider_error():
    router = AsyncMock()
    router.send = AsyncMock(side_effect=RuntimeError("provider down"))

    analysis = await analyze_turn_with_llm(
        "그럼 현재 순위는?",
        recent_messages=[],
        router=router,
        max_tokens=256,
    )

    assert analysis.normalized_question == "그럼 현재 순위는?"
    assert analysis.source == "fallback"
    assert analysis.route == ResponseRoute.STANDARD_TOOL_LOOP


@pytest.mark.asyncio
async def test_analyze_turn_with_llm_falls_back_on_invalid_json():
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text="순위는 다음과 같습니다 (JSON 아님)")
    )

    analysis = await analyze_turn_with_llm(
        "그럼 현재 순위는?", recent_messages=[], router=router
    )

    assert analysis.source == "fallback"
    assert analysis.normalized_question == "그럼 현재 순위는?"


# ----------------------------------------------------------------------
# BIZ-427 — TURN_ANALYSIS_RESPONSE_SCHEMA / structured output 요청
# ----------------------------------------------------------------------


def test_turn_analysis_response_schema_has_required_route_enum_and_ordering():
    schema = TURN_ANALYSIS_RESPONSE_SCHEMA
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["route"]["enum"]) == {
        route.value for route in ResponseRoute
    }
    assert "propertyOrdering" in schema
    assert schema["required"] == schema["propertyOrdering"]
    assert "normalized_question" in schema["required"]
    # 모든 property 가 required/propertyOrdering 에 빠짐없이 포함되어야 한다.
    assert set(schema["properties"].keys()) == set(schema["required"])


_VALID_ANALYSIS_JSON = (
    '{"is_followup":false,"normalized_question":"안녕","context_summary":"",'
    '"confidence":1,"needs_clarification":false,"ambiguity_options":[],'
    '"domains":[],"intents":[],"route":"standard_tool_loop",'
    '"complexity_score":0,"needs_current_facts":false,"needs_rules":false,'
    '"needs_remaining_variables":false,"needs_calculation":false,'
    '"needs_comparison_or_conditions":false,"needs_conflict_resolution":false,'
    '"needs_impact_analysis":false,"reasons":["greeting"]}'
)


@pytest.mark.asyncio
async def test_analyze_turn_with_llm_requests_structured_json_schema():
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_VALID_ANALYSIS_JSON))

    await analyze_turn_with_llm(
        "안녕",
        recent_messages=[],
        router=router,
        max_tokens=256,
    )

    request = router.send.call_args.args[0]
    assert request.response_mime_type == "application/json"
    assert request.response_schema == TURN_ANALYSIS_RESPONSE_SCHEMA
    assert request.require_structured_output is True


@pytest.mark.asyncio
async def test_analyze_turn_with_llm_structured_output_false_keeps_plain_request():
    """escape hatch — structured_output=False 면 기존 프롬프트-only 요청 유지."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_VALID_ANALYSIS_JSON))

    await analyze_turn_with_llm(
        "안녕",
        recent_messages=[],
        router=router,
        structured_output=False,
    )

    request = router.send.call_args.args[0]
    assert request.response_mime_type is None
    assert request.response_schema is None
    assert request.require_structured_output is False


@pytest.mark.asyncio
async def test_turn_analysis_fallback_logs_structured_output_context(caplog):
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text='{"bad": '))

    with caplog.at_level(
        logging.WARNING, logger="simpleclaw.agent.turn_analysis"
    ):
        analysis = await analyze_turn_with_llm(
            "새로 알게 된 내용 이야기해줘봐",
            recent_messages=[],
            router=router,
        )

    assert analysis.source == "fallback"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "structured" in joined.lower()
    assert "raw_len=" in joined
    assert "backend=turn_analysis" in joined
    # raw 응답 전문(사용자 발화 포함 가능)은 로그에 노출하지 않는다.
    assert '{"bad":' not in joined


@pytest.mark.asyncio
async def test_turn_analysis_fallback_does_not_log_exception_text(caplog):
    """BIZ-430 — provider 예외 메시지가 사용자 발화/raw payload 를 담고 있어도
    fallback warning 에는 예외 원문이 노출되지 않아야 한다."""
    secret = "사용자 비밀 원문"
    raw_like = '{"normalized_question": "leaked"}'
    router = AsyncMock()
    router.send = AsyncMock(
        side_effect=RuntimeError(f"provider echoed {secret} {raw_like}")
    )

    with caplog.at_level(
        logging.WARNING, logger="simpleclaw.agent.turn_analysis"
    ):
        analysis = await analyze_turn_with_llm(
            secret,
            recent_messages=[],
            router=router,
        )

    assert analysis.source == "fallback"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    # 예외 원문(사용자 발화·raw-like payload 포함)은 어디에도 없어야 한다.
    assert secret not in joined
    assert raw_like not in joined
    assert "provider echoed" not in joined
    # 안전 메타데이터(예외 타입, structured 플래그, raw 길이, backend)만 남는다.
    assert "error_type=RuntimeError" in joined
    assert "structured=True" in joined
    assert "raw_len=0" in joined
    assert "backend=turn_analysis" in joined
