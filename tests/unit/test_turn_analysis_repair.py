"""BIZ-452 — TurnAnalysis truncated-tail repair 및 fallback backend 재시도 테스트.

live trace(83b90b454ed04f5daf6cab296c7b8674)에서 DeepSeek structured JSON 이
tail `reasons` 문자열 중간의 출력 토큰 cap 에서 잘려 JSONDecodeError 로 파싱이
실패했고, 핵심 판단(route=complex_fact_workflow, complexity_score=8)이 이미
내려졌음에도 conservative fallback 으로 낮게 분기됐다. 이 파일은:

1. truncated payload 의 tail repair 가 핵심 필드를 보존하는지
2. 핵심 필드 이전에서 잘린 payload 는 라우팅을 지어내지 않는지
3. repair 실패 시 fallback backend 1회 재시도가 동작하는지
4. repair/retry/fallback 로그가 raw 본문 없이 진단 메타데이터만 남기는지
를 검증한다.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.response_router import ResponseRoute
from simpleclaw.agent.turn_analysis import (
    analyze_turn_with_llm,
    repair_turn_analysis_payload,
)
from simpleclaw.llm.models import LLMResponse

# live 재현 shape — 핵심 필드는 완결됐고 tail `reasons` 문자열 중간에서 잘렸다.
_TRUNCATED_LIVE_LIKE = (
    '{"is_followup":true,'
    '"normalized_question":"현재 중동 전쟁 상황이 주가에 어떤 영향을 줄지 예측 레포트를 작성해줘",'
    '"context_summary":"직전 대화는 전쟁 뉴스와 주식 시장 흐름이었다",'
    '"confidence":0.9,"needs_clarification":false,"ambiguity_options":[],'
    '"domains":["market","news"],"intents":["scenario_analysis","realtime_lookup"],'
    '"route":"complex_fact_workflow","complexity_score":8,'
    '"needs_current_facts":true,"needs_rules":false,"needs_remaining_variables":true,'
    '"needs_calculation":false,"needs_comparison_or_conditions":true,'
    '"needs_conflict_resolution":false,"needs_impact_analysis":true,'
    '"reasons":["war and market interact across multiple evidence slots",'
    '"needs current prices and geopoliti'
)

_VALID_ANALYSIS_JSON = (
    '{"is_followup":true,"normalized_question":"전쟁 상황의 주가 영향 예측 레포트",'
    '"context_summary":"전쟁 뉴스 맥락","confidence":0.85,'
    '"needs_clarification":false,"ambiguity_options":[],'
    '"domains":["market"],"intents":["scenario_analysis"],'
    '"route":"complex_fact_workflow","complexity_score":7,'
    '"needs_current_facts":true,"needs_rules":false,'
    '"needs_remaining_variables":false,"needs_calculation":false,'
    '"needs_comparison_or_conditions":true,"needs_conflict_resolution":false,'
    '"needs_impact_analysis":true,"reasons":["scenario analysis"]}'
)


# ----------------------------------------------------------------------
# repair_turn_analysis_payload
# ----------------------------------------------------------------------


def test_repair_preserves_core_fields_from_truncated_reasons_tail():
    """tail `reasons` 중간에서 잘려도 핵심 라우팅 판단은 그대로 살아난다."""
    analysis = repair_turn_analysis_payload(
        _TRUNCATED_LIVE_LIKE, original_text="그러면, 주가에 영향 예측 레포트 작성해봐"
    )

    assert analysis is not None
    assert analysis.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert analysis.complexity_score == 8
    assert analysis.confidence == 0.9
    assert analysis.is_followup is True
    assert analysis.domains == ("market", "news")
    assert analysis.intents == ("scenario_analysis", "realtime_lookup")
    assert analysis.needs_current_facts is True
    assert analysis.needs_remaining_variables is True
    assert analysis.needs_comparison_or_conditions is True
    assert analysis.needs_impact_analysis is True
    assert analysis.needs_rules is False
    assert analysis.source == "llm"
    # 잘린 마지막 reason 항목은 버려지고 완결된 항목만 남는다.
    assert analysis.reasons == (
        "war and market interact across multiple evidence slots",
    )


def test_repair_handles_cut_inside_reasons_array_opening():
    """`"reasons":[` 직후 truncation — 완결 reason 이 하나도 없어도 복구된다."""
    payload = _TRUNCATED_LIVE_LIKE.split('"reasons":[')[0] + '"reasons":['
    analysis = repair_turn_analysis_payload(payload, original_text="원문")

    assert analysis is not None
    assert analysis.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert analysis.complexity_score == 8
    assert analysis.reasons == ()


def test_repair_rejects_payload_cut_before_core_fields():
    """route/complexity_score 이전에서 잘린 payload 는 라우팅을 지어내지 않는다."""
    payload = (
        '{"is_followup":true,"normalized_question":"뭔가 질문",'
        '"context_summary":"직전 대화는 전쟁 뉴'
    )
    assert repair_turn_analysis_payload(payload, original_text="원문") is None


def test_repair_rejects_non_object_payload():
    assert repair_turn_analysis_payload('["not","an","obj', original_text="x") is None
    assert repair_turn_analysis_payload("plain text", original_text="x") is None
    assert repair_turn_analysis_payload("", original_text="x") is None


def test_repair_clamps_overlong_context_summary_and_reasons():
    """repair 경로에서도 설명 필드는 짧게 clamp 된다 (BIZ-452)."""
    long_summary = "가" * 1000
    payload = (
        '{"context_summary":"' + long_summary + '",'
        '"route":"standard_tool_loop","complexity_score":1,'
        '"reasons":["' + "b" * 500 + '","r2","r3","r4","r5'
    )
    analysis = repair_turn_analysis_payload(payload, original_text="원문")

    assert analysis is not None
    assert len(analysis.context_summary) <= 240
    assert len(analysis.reasons) <= 3
    assert all(len(reason) <= 160 for reason in analysis.reasons)


# ----------------------------------------------------------------------
# analyze_turn_with_llm — repair / retry / fallback 흐름
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_repairs_truncated_payload_without_retry():
    """repair 가능한 truncation 은 재시도 없이 1차 응답에서 복구한다."""
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=_TRUNCATED_LIVE_LIKE, finish_reason="length")
    )

    analysis = await analyze_turn_with_llm(
        "그러면, 주가에 현재 상황이 어떤 영향을 줄지 예측 레포트 작성해봐",
        recent_messages=[],
        router=router,
    )

    assert analysis.source == "llm"
    assert analysis.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert analysis.complexity_score == 8
    # repair 로 충분하므로 fallback backend 재시도는 발생하지 않는다.
    router.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_retries_route_once_when_repair_fails():
    """Malformed non-empty primary responses consume the route retry once."""
    from simpleclaw.llm.models import LLMRoute
    from simpleclaw.llm.router import LLMRouter

    primary = AsyncMock()
    primary.send = AsyncMock(
        return_value=LLMResponse(text="깨진 응답 (JSON 아님)", finish_reason="length")
    )
    retry = AsyncMock()
    retry.send = AsyncMock(return_value=LLMResponse(text=_VALID_ANALYSIS_JSON))
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        default_backend="primary",
        routes={
            "default": LLMRoute("default", "primary"),
            "turn_analysis": LLMRoute("turn_analysis", "primary", "retry"),
        },
    )

    analysis = await analyze_turn_with_llm(
        "주가 영향 예측 레포트 작성해봐",
        recent_messages=[],
        router=router,
    )

    assert analysis.source == "llm"
    assert analysis.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    primary.send.assert_awaited_once()
    retry.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_falls_back_when_repair_fails(caplog):
    from simpleclaw.llm.models import LLMRoute
    from simpleclaw.llm.router import LLMRouter

    primary = AsyncMock()
    primary.send = AsyncMock(return_value=LLMResponse(text="깨진 응답 1", finish_reason="length"))
    retry = AsyncMock()
    retry.send = AsyncMock(side_effect=RuntimeError("fallback backend down"))
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        default_backend="primary",
        routes={"turn_analysis": LLMRoute("turn_analysis", "primary", "retry")},
    )

    with caplog.at_level(logging.WARNING, logger="simpleclaw.agent.turn_analysis"):
        analysis = await analyze_turn_with_llm(
            "주가 영향 예측 레포트",
            recent_messages=[],
            router=router,
        )

    assert analysis.source == "fallback"
    assert analysis.route == ResponseRoute.STANDARD_TOOL_LOOP
    primary.send.assert_awaited_once()
    retry.send.assert_awaited_once()
    joined = "\n".join(record.getMessage() for record in caplog.records)
    # 진단 메타데이터만 남고 raw 본문/원문은 노출되지 않는다.
    assert "backend=turn_analysis" in joined
    assert "repair_status=failed" in joined
    assert "깨진 응답" not in joined
    assert "주가 영향" not in joined


@pytest.mark.asyncio
async def test_analyze_skips_retry_when_retry_backend_equals_primary():
    """retry backend 가 1차 backend 와 같으면 무의미한 재시도를 하지 않는다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text="not json"))

    analysis = await analyze_turn_with_llm(
        "질문",
        recent_messages=[],
        router=router,
    )

    assert analysis.source == "fallback"
    router.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_skips_retry_when_no_retry_backend_configured():
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text="not json"))

    analysis = await analyze_turn_with_llm(
        "질문", recent_messages=[], router=router
    )

    assert analysis.source == "fallback"
    router.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_repair_logs_are_sanitized(caplog):
    """repair 성공 로그에도 raw 본문 없이 안전 메타데이터만 남는다."""
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=_TRUNCATED_LIVE_LIKE, finish_reason="length")
    )

    with caplog.at_level(logging.WARNING, logger="simpleclaw.agent.turn_analysis"):
        analysis = await analyze_turn_with_llm(
            "주가 영향 예측 레포트", recent_messages=[], router=router
        )

    assert analysis.source == "llm"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "repair_status=repaired" in joined
    assert "error_type=JSONDecodeError" in joined
    assert "finish_reason=length" in joined
    assert f"raw_len={len(_TRUNCATED_LIVE_LIKE)}" in joined
    # raw payload 조각(사용자/모델 본문)은 로그 어디에도 없어야 한다.
    assert "war and market" not in joined
    assert "중동 전쟁" not in joined


@pytest.mark.asyncio
async def test_live_like_market_followup_keeps_complex_route_despite_truncation():
    """live 재현 — market impact follow-up 이 truncation 때문에
    current_fact_guarded_loop/fallback 으로 낮아지지 않는다."""
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=_TRUNCATED_LIVE_LIKE, finish_reason="length")
    )

    analysis = await analyze_turn_with_llm(
        "그러면, 주가에 현재 상황이 어떤 영향을 줄지 예측 레포트 작성해봐",
        recent_messages=[
            {"role": "user", "content": "지금 중동 전쟁 상황 정리해줘"},
            {"role": "assistant", "content": "현재 전쟁 상황은 다음과 같습니다..."},
        ],
        router=router,
    )

    assert analysis.source == "llm"
    assert analysis.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    decision = analysis.to_route_decision()
    assert decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
    assert decision.needs_impact_analysis is True
    assert decision.complexity_score == 8
