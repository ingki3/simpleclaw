"""TurnAnalysis schema 의 OpenAI-compatible structured output 호환성 테스트 (BIZ-450).

실제 TurnAnalysis 요청 shape(`TURN_ANALYSIS_RESPONSE_SCHEMA`)가 OpenAIProvider
를 통해 `response_format.type=json_schema` 로 나가고, Gemini 전용 확장 키
(`propertyOrdering`)가 제거되는지 검증한다 — live 에서 DeepSeek default 가
TurnAnalysis 를 Gemini fallback 없이 처리하기 위한 계약.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.llm.providers.openai_provider import OpenAIProvider


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
                    )
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

    await provider.send(
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
