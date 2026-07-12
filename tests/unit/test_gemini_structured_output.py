"""BIZ-427 — Gemini structured output config 매핑 테스트.

fake Gemini client 로 ``GeminiProvider.send()`` 가 structured output 힌트를
``GenerateContentConfig.response_mime_type`` / ``response_schema`` 로 매핑하는지,
``stream()`` 이 required structured output 오용을 명확히 거부하는지 검증한다.
실제 Gemini API 는 호출하지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simpleclaw.llm.models import LLMProviderError
from simpleclaw.llm.providers.gemini import GeminiProvider

_SCHEMA = {
    "type": "object",
    "properties": {"route": {"type": "string"}},
    "required": ["route"],
}


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
        model="gemini-2.5-flash", api_key="test", name="gemini"
    )
    fake_models = _FakeModels()
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    return provider, fake_models


class TestGeminiSendStructuredOutput:
    @pytest.mark.asyncio
    async def test_send_sets_response_mime_type_and_schema(self):
        provider, fake_models = _build_provider()

        await provider.send(
            system_prompt="classify",
            user_message="hello",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=True,
        )

        config = fake_models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema == _SCHEMA

    @pytest.mark.asyncio
    async def test_send_without_structured_fields_keeps_config_untouched(self):
        """미지정 시 config 에 structured output 필드가 세팅되지 않아야 한다 (회귀 0)."""
        provider, fake_models = _build_provider()

        await provider.send(system_prompt="sys", user_message="hello")

        config = fake_models.calls[0]["config"]
        assert config.response_mime_type is None
        assert config.response_schema is None

    @pytest.mark.asyncio
    async def test_send_required_but_incomplete_hints_raises(self):
        """required 인데 mime/schema 가 반쪽이면 API 호출 전에 실패해야 한다."""
        provider, fake_models = _build_provider()

        with pytest.raises(LLMProviderError):
            await provider.send(
                system_prompt="classify",
                user_message="hello",
                response_mime_type="application/json",
                response_schema=None,
                require_structured_output=True,
            )
        assert fake_models.calls == []

    @pytest.mark.asyncio
    async def test_structured_output_response_text_is_returned(self):
        """structured 응답도 기존과 동일하게 LLMResponse 로 반환되어야 한다."""
        provider, _ = _build_provider()

        response = await provider.send(
            system_prompt="classify",
            user_message="hello",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=True,
        )

        assert response.text == '{"route":"standard_tool_loop"}'
        assert response.usage == {"input_tokens": 1, "output_tokens": 1}


class TestGeminiStreamStructuredOutputGuard:
    @pytest.mark.asyncio
    async def test_stream_rejects_required_structured_output(self):
        provider, _ = _build_provider()

        with pytest.raises(LLMProviderError):
            await provider.stream(
                system_prompt="classify",
                user_message="hello",
                response_mime_type="application/json",
                response_schema={"type": "object"},
                require_structured_output=True,
            )

    @pytest.mark.asyncio
    async def test_stream_rejects_required_without_hints(self):
        """BIZ-430 — required 계약은 힌트 유무와 무관하다. 힌트 없는 required
        스트리밍 요청도 API 호출 전에 즉시 거부해야 한다."""
        provider, _ = _build_provider()

        with pytest.raises(LLMProviderError):
            await provider.stream(
                system_prompt="classify",
                user_message="hello",
                require_structured_output=True,
            )
