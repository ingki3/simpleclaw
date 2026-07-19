"""BIZ-454 — Gemini response_schema provider 전용 sanitizer 테스트.

live smoke 에서 `TURN_ANALYSIS_RESPONSE_SCHEMA` 의 ``additionalProperties`` 가
SDK payload 의 ``additional_properties`` 로 변환되어 Gemini API 가
``400 INVALID_ARGUMENT`` 를 반환한 사고의 재발 방지 회귀를 고정한다.

검증 계약:
  - Gemini 로 나가는 schema 에서 root/nested ``additionalProperties`` 만 제거.
  - 원본 schema dict 는 불변 (OpenAI-compatible 경로가 계속 사용).
  - ``propertyOrdering``/``required``/``enum``/``items`` 등 지원 필드 보존.
  - structured output + reasoning hint 동시 전달 가능 (off / medium 모두).
실제 Gemini API 는 호출하지 않는다.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.llm.providers.gemini import GeminiProvider

_NESTED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "route": {"type": "string", "enum": ["a", "b"]},
        "detail": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"score": {"type": "integer", "minimum": 0}},
            "required": ["score"],
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"label": {"type": "string"}},
            },
            "maxItems": 4,
        },
    },
    "required": ["route"],
    "propertyOrdering": ["route", "detail", "options"],
}


def _contains_key(node: object, key: str) -> bool:
    """dict/list 트리 어디든 key 가 남아 있는지 재귀 확인한다."""
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


class _FakeModels:
    """generate_content 호출 kwargs 를 기록하는 fake — config 검증용."""

    def __init__(self):
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                text='{"route":"standard_tool_loop"}',
                                function_call=None,
                            )
                        ]
                    ),
                    finish_reason=None,
                )
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=1, candidates_token_count=1
            ),
            prompt_feedback=None,
        )


def _build_provider() -> tuple[GeminiProvider, _FakeModels]:
    provider = GeminiProvider(
        model="gemini-3.5-flash", api_key="test", name="gemini"
    )
    fake_models = _FakeModels()
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    return provider, fake_models


class TestSanitizeResponseSchema:
    def test_removes_root_and_nested_additional_properties(self):
        sanitized = GeminiProvider._sanitize_response_schema(_NESTED_SCHEMA)

        assert not _contains_key(sanitized, "additionalProperties")

    def test_preserves_supported_schema_fields(self):
        sanitized = GeminiProvider._sanitize_response_schema(_NESTED_SCHEMA)

        assert sanitized["propertyOrdering"] == ["route", "detail", "options"]
        assert sanitized["required"] == ["route"]
        assert sanitized["properties"]["route"]["enum"] == ["a", "b"]
        assert sanitized["properties"]["detail"]["required"] == ["score"]
        assert sanitized["properties"]["detail"]["properties"]["score"] == {
            "type": "integer",
            "minimum": 0,
        }
        assert sanitized["properties"]["options"]["maxItems"] == 4
        assert sanitized["properties"]["options"]["items"]["properties"] == {
            "label": {"type": "string"}
        }

    def test_original_schema_is_not_mutated(self):
        source = copy.deepcopy(_NESTED_SCHEMA)

        GeminiProvider._sanitize_response_schema(source)

        assert source == _NESTED_SCHEMA
        assert source["additionalProperties"] is False

    def test_non_dict_schema_passes_through_unchanged(self):
        """pydantic 타입 등 dict 가 아닌 schema 는 SDK 변환에 맡긴다."""

        class _Marker:
            pass

        assert GeminiProvider._sanitize_response_schema(_Marker) is _Marker

    def test_turn_analysis_schema_becomes_gemini_compatible(self):
        """live 사고 재현 입력 — 실제 TurnAnalysis schema 가 정리되는지 확인."""
        before = copy.deepcopy(TURN_ANALYSIS_RESPONSE_SCHEMA)

        sanitized = GeminiProvider._sanitize_response_schema(
            TURN_ANALYSIS_RESPONSE_SCHEMA
        )

        assert not _contains_key(sanitized, "additionalProperties")
        assert sanitized["propertyOrdering"] == before["propertyOrdering"]
        assert sanitized["required"] == before["required"]
        assert set(sanitized["properties"]) == set(before["properties"])
        # 원본은 OpenAI-compatible 경로를 위해 그대로 남는다 (BIZ-450).
        assert TURN_ANALYSIS_RESPONSE_SCHEMA == before
        assert TURN_ANALYSIS_RESPONSE_SCHEMA["additionalProperties"] is False


class TestGeminiSendSanitizedSchema:
    @pytest.mark.asyncio
    async def test_send_strips_additional_properties_from_config(self):
        provider, fake_models = _build_provider()

        await provider.send(
            system_prompt="analyze",
            user_message="hello",
            response_mime_type="application/json",
            response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
            require_structured_output=True,
        )

        config = fake_models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert not _contains_key(config.response_schema, "additionalProperties")
        assert (
            config.response_schema["propertyOrdering"]
            == TURN_ANALYSIS_RESPONSE_SCHEMA["propertyOrdering"]
        )
        # send() 호출 후에도 원본 schema 는 불변이다.
        assert TURN_ANALYSIS_RESPONSE_SCHEMA["additionalProperties"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reasoning",
        [
            None,
            {"enabled": False},
            {"enabled": True, "effort": "medium", "budget_tokens": 512},
        ],
        ids=["reasoning-unset", "reasoning-off", "reasoning-medium-512"],
    )
    async def test_structured_output_works_with_reasoning_hint(self, reasoning):
        """live 실패 조합 — reasoning off/medium 모두 sanitized schema 로 전달."""
        provider, fake_models = _build_provider()

        response = await provider.send(
            system_prompt="analyze",
            user_message="hello",
            response_mime_type="application/json",
            response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
            require_structured_output=True,
            reasoning=reasoning,
        )

        config = fake_models.calls[0]["config"]
        assert not _contains_key(config.response_schema, "additionalProperties")
        if reasoning and reasoning.get("enabled"):
            assert config.thinking_config.thinking_budget == 512
        assert response.text == '{"route":"standard_tool_loop"}'
