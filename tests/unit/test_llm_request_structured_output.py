"""BIZ-427 — provider-neutral structured output 요청 모델/라우터 전달 테스트.

``LLMRequest`` 가 structured output 필드(response_mime_type/response_schema/
require_structured_output)를 보존하고, ``LLMRouter`` 가 send/stream 양 경로에서
해당 필드를 provider kwargs 로 끊김 없이 전달하는지 검증한다. 미지정 시
기본값(None/False)이 유지되어 기존 호출처 회귀가 0 임도 함께 확인한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.models import BackendType, LLMBackend, LLMRequest, LLMResponse
from simpleclaw.llm.router import LLMRouter

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


# ---------------------------------------------------------------------------
# 1. LLMRequest 모델 자체
# ---------------------------------------------------------------------------


class TestLLMRequestStructuredOutputFields:
    def test_defaults_keep_structured_output_disabled(self):
        """미지정 시 구조화 출력 필드는 모두 비활성 — 기존 호출처 회귀 0."""
        req = LLMRequest(user_message="hi")
        assert req.response_mime_type is None
        assert req.response_schema is None
        assert req.require_structured_output is False

    def test_accepts_structured_output_fields(self):
        """전달된 structured output 필드가 그대로 보존되어야 한다."""
        req = LLMRequest(
            system_prompt="classify",
            user_message="hello",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=True,
        )
        assert req.response_mime_type == "application/json"
        assert req.response_schema == _SCHEMA
        assert req.require_structured_output is True


# ---------------------------------------------------------------------------
# 2. LLMRouter — send/stream 양 경로 provider 전달
# ---------------------------------------------------------------------------


def _build_router(provider: AsyncMock) -> LLMRouter:
    return LLMRouter(
        backends={
            "gemini": LLMBackend(
                name="gemini",
                backend_type=BackendType.API,
                model="gemini-2.5-flash",
            )
        },
        providers={"gemini": provider},
        default_backend="gemini",
    )


class TestRouterStructuredOutputPassThrough:
    @pytest.mark.asyncio
    async def test_send_passes_structured_output_fields(self):
        provider = AsyncMock()
        provider.send = AsyncMock(return_value=LLMResponse(text='{"answer":"ok"}'))
        router = _build_router(provider)

        await router.send(
            LLMRequest(
                user_message="x",
                response_mime_type="application/json",
                response_schema=_SCHEMA,
                require_structured_output=True,
            )
        )

        kwargs = provider.send.call_args.kwargs
        assert kwargs["response_mime_type"] == "application/json"
        assert kwargs["response_schema"] == _SCHEMA
        assert kwargs["require_structured_output"] is True

    @pytest.mark.asyncio
    async def test_send_defaults_pass_disabled_fields(self):
        """미지정 요청도 kwargs 로는 항상 흘러가되 비활성 값이어야 한다."""
        provider = AsyncMock()
        provider.send = AsyncMock(return_value=LLMResponse(text="ok"))
        router = _build_router(provider)

        await router.send(LLMRequest(user_message="x"))

        kwargs = provider.send.call_args.kwargs
        assert kwargs["response_mime_type"] is None
        assert kwargs["response_schema"] is None
        assert kwargs["require_structured_output"] is False

    @pytest.mark.asyncio
    async def test_stream_passes_structured_output_fields(self):
        provider = AsyncMock()
        provider.stream = AsyncMock(return_value=LLMResponse(text="ok"))
        router = _build_router(provider)

        async def _on_delta(_: str) -> None:
            pass

        await router.send(
            LLMRequest(
                user_message="x",
                response_mime_type="application/json",
                response_schema=_SCHEMA,
                require_structured_output=True,
            ),
            on_text_delta=_on_delta,
        )

        kwargs = provider.stream.call_args.kwargs
        assert kwargs["response_mime_type"] == "application/json"
        assert kwargs["response_schema"] == _SCHEMA
        assert kwargs["require_structured_output"] is True
