"""BIZ-453 — Gemini provider-neutral reasoning hint 매핑 테스트.

fake Gemini client 로 ``GeminiProvider.send()`` 가 reasoning hint 를
``GenerateContentConfig.thinking_config`` 로 매핑하는지, structured output 과
동시에 동작하는지, installed SDK 미지원 시 no-op 으로 degrade 하는지 검증한다.
실제 Gemini API 는 호출하지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simpleclaw.llm.providers import gemini as gemini_module
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
        model="gemini-3.5-flash", api_key="test", name="gemini"
    )
    fake_models = _FakeModels()
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=fake_models))
    return provider, fake_models


class TestGeminiReasoningHint:
    @pytest.mark.asyncio
    async def test_enabled_hint_maps_budget_tokens_to_thinking_config(self):
        provider, fake_models = _build_provider()

        await provider.send(
            system_prompt="sys",
            user_message="hello",
            reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
        )

        config = fake_models.calls[0]["config"]
        assert config.thinking_config is not None
        assert config.thinking_config.thinking_budget == 512

    @pytest.mark.asyncio
    async def test_missing_budget_falls_back_to_effort_mapping(self):
        provider, fake_models = _build_provider()

        await provider.send(
            system_prompt="sys",
            user_message="hello",
            reasoning={"enabled": True, "effort": "high"},
        )

        config = fake_models.calls[0]["config"]
        assert config.thinking_config.thinking_budget == 1024

    @pytest.mark.asyncio
    async def test_disabled_or_absent_hint_is_noop(self):
        provider, fake_models = _build_provider()

        await provider.send(system_prompt="sys", user_message="hello")
        await provider.send(
            system_prompt="sys",
            user_message="hello",
            reasoning={"enabled": False, "budget_tokens": 512},
        )

        assert fake_models.calls[0]["config"].thinking_config is None
        assert fake_models.calls[1]["config"].thinking_config is None

    @pytest.mark.asyncio
    async def test_reasoning_and_structured_output_coexist(self):
        """BIZ-453 — thinking config 가 response_schema 매핑을 밀어내지 않는다."""
        provider, fake_models = _build_provider()

        await provider.send(
            system_prompt="classify",
            user_message="hello",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=True,
            reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
        )

        config = fake_models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema == _SCHEMA
        assert config.thinking_config.thinking_budget == 512

    @pytest.mark.asyncio
    async def test_unsupported_sdk_degrades_to_noop(self, monkeypatch, caplog):
        """installed SDK 가 ThinkingConfig 를 거부하면 요청은 그대로 성공해야 한다."""
        provider, fake_models = _build_provider()

        class _Unsupported:
            def __init__(self, **kwargs):
                raise TypeError("thinking_budget is not a valid field")

        monkeypatch.setattr(
            gemini_module.types, "ThinkingConfig", _Unsupported
        )

        with caplog.at_level("WARNING"):
            response = await provider.send(
                system_prompt="sys",
                user_message="hello",
                reasoning={"enabled": True, "budget_tokens": 512},
            )

        assert response.text
        assert fake_models.calls[0]["config"].thinking_config is None
        # sanitized diagnostic — 사용자 원문/응답 본문 없이 타입 이름만 남긴다.
        assert any(
            "reasoning hint unsupported" in record.getMessage()
            for record in caplog.records
        )
