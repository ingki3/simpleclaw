"""TurnAnalysis schema 의 OpenAI-compatible structured output 호환성 테스트 (BIZ-450).

실제 TurnAnalysis 요청 shape(`TURN_ANALYSIS_RESPONSE_SCHEMA`)가 OpenAIProvider
를 통해 `response_format.type=json_schema` 로 나가고, Gemini 전용 확장 키
(`propertyOrdering`)가 제거되는지 검증한다 — live 에서 DeepSeek default 가
TurnAnalysis 를 Gemini fallback 없이 처리하기 위한 계약.

BIZ-452 — `choice.finish_reason` 이 `LLMResponse.finish_reason` 으로 보존되어
출력 토큰 cap truncation(finish_reason=length)을 raw 응답 없이 진단할 수 있는
계약도 함께 검증한다.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.agent.turn_plan import UNIFIED_TURN_PLAN_RESPONSE_SCHEMA
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.openai_provider import OpenAIProvider


def _contains_key(node: object, key: str) -> bool:
    """schema tree 어디든 지정 key가 남아 있는지 재귀 확인한다."""
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


@pytest.mark.asyncio
async def test_openai_provider_accepts_turn_analysis_schema(monkeypatch):
    create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=(
                            '{"is_followup":false,"normalized_question":"hello",'
                            '"context_summary":"","confidence":1.0,'
                            '"needs_clarification":false,"ambiguity_options":[],'
                            '"domains":[],"intents":[],"route":"standard_tool_loop",'
                            '"complexity_score":0,"needs_current_facts":false,'
                            '"needs_rules":false,"needs_remaining_variables":false,'
                            '"needs_calculation":false,"needs_comparison_or_conditions":false,'
                            '"needs_conflict_resolution":false,"needs_impact_analysis":false,'
                            '"reasons":[]}'
                        ),
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=MagicMock(prompt_tokens=10, completion_tokens=20),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(
        model="deepseek/deepseek-v4-pro",
        api_key="test-key",
        name="openrouter_deepseek_v4_pro",
        extra_body={"reasoning": {"enabled": False}},
    )

    response = await provider.send(
        system_prompt="analyze",
        user_message="hello",
        response_mime_type="application/json",
        response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
        require_structured_output=True,
    )

    response_format = create.call_args.kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert "propertyOrdering" not in schema
    assert schema["additionalProperties"] is False
    assert "route" in schema["properties"]
    # 원본 스키마는 변형 없이 그대로 유지되어야 한다 — Gemini 경로가 계속 사용.
    assert "propertyOrdering" in TURN_ANALYSIS_RESPONSE_SCHEMA
    # BIZ-452 — 종료 사유가 LLMResponse 에 보존된다.
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_openai_provider_preserves_length_finish_reason(monkeypatch, caplog):
    """출력 cap truncation(finish_reason=length)이 응답과 sanitized 로그에 남는다."""
    truncated_json = '{"is_followup":true,"route":"complex_fact_workflow","reasons":["cut mid strin'
    create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content=truncated_json, tool_calls=None),
                    finish_reason="length",
                )
            ],
            usage=MagicMock(prompt_tokens=10, completion_tokens=512),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )

    provider = OpenAIProvider(
        model="deepseek/deepseek-v4-pro",
        api_key="test-key",
        name="openrouter_deepseek_v4_pro",
    )

    with caplog.at_level(
        logging.WARNING, logger="simpleclaw.llm.providers.openai_provider"
    ):
        response = await provider.send(
            system_prompt="analyze",
            user_message="user secret question",
            response_mime_type="application/json",
            response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
            require_structured_output=True,
        )

    assert response.finish_reason == "length"
    assert response.diagnostics == {"finish_reason": "length"}
    # response_format 계약은 유지된다.
    assert create.call_args.kwargs["response_format"]["type"] == "json_schema"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    # truncation 경고는 남되, 프롬프트/응답 본문은 노출되지 않는다.
    assert "finish_reason=length" in joined
    assert "user secret question" not in joined
    assert "cut mid strin" not in joined


@pytest.mark.asyncio
async def test_openai_provider_accepts_unified_turn_plan_schema(monkeypatch):
    """Unified schema의 Gemini/OpenAI dialect가 같은 필드 계약을 보존한다."""
    create = AsyncMock(
        return_value=MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content="{}", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=MagicMock(prompt_tokens=10, completion_tokens=2),
        )
    )
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )
    provider = OpenAIProvider(
        model="deepseek/deepseek-v4-pro",
        api_key="test-key",
        name="openrouter_deepseek_v4_pro",
    )

    await provider.send(
        system_prompt="plan",
        user_message="hello",
        response_mime_type="application/json",
        response_schema=UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
        require_structured_output=True,
    )

    response_format = create.call_args.kwargs["response_format"]
    schema = response_format["json_schema"]["schema"]
    assert response_format["type"] == "json_schema"
    assert schema["additionalProperties"] is False
    assert "propertyOrdering" not in schema
    assert "propertyOrdering" in UNIFIED_TURN_PLAN_RESPONSE_SCHEMA
    assert schema["properties"]["execution"]["additionalProperties"] is False

    gemini_schema = get_provider_profile("gemini").adapt_schema(
        UNIFIED_TURN_PLAN_RESPONSE_SCHEMA
    )
    assert not _contains_key(gemini_schema, "additionalProperties")
    assert not _contains_key(schema, "propertyOrdering")
    assert gemini_schema["propertyOrdering"] == schema["required"]
    assert set(gemini_schema["properties"]) == set(schema["properties"])
    assert (
        gemini_schema["properties"]["execution"]["required"]
        == schema["properties"]["execution"]["required"]
    )
