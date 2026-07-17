"""LLMRouter default/fallback/multimodal 라우팅 정책 테스트 (BIZ-448).

정책 요약:
  - 암묵적(text, 첨부 없음) 요청 → default 백엔드.
  - 첨부 포함 암묵적 요청 → multimodal 백엔드.
  - default 백엔드 provider 예외 또는 empty final → fallback 백엔드 1회 재시도.
  - explicit backend_name 요청 → 자동 fallback 금지.
  - 스트리밍(on_text_delta) 경로는 fallback 하지 않음 — 혼합 출력 방지.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.models import (
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    MultimodalAttachment,
)
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter


class DummyProvider(LLMProvider):
    """send/stream 을 인스턴스 AsyncMock 으로 대체하는 테스트용 provider."""

    async def send(self, *args, **kwargs):  # pragma: no cover — ABC 충족용
        raise NotImplementedError

    def __init__(self, name: str, text: str = "ok") -> None:
        self.name = name
        self.send = AsyncMock(
            return_value=LLMResponse(text=text, backend_name=name, model="m")
        )
        self.stream = AsyncMock(
            return_value=LLMResponse(text=text, backend_name=name, model="m")
        )


def _make_router(glm: DummyProvider, gemini: DummyProvider) -> LLMRouter:
    return LLMRouter(
        backends={},
        providers={"openrouter_glm_5_2": glm, "gemini": gemini},
        default_backend="openrouter_glm_5_2",
        fallback_backend="gemini",
        multimodal_backend="gemini",
    )


def test_router_exposes_configured_routing_policy():
    router = _make_router(
        DummyProvider("openrouter_glm_5_2"), DummyProvider("gemini")
    )

    assert router.get_default_backend() == "openrouter_glm_5_2"
    assert router.get_fallback_backend() == "gemini"
    assert router.get_multimodal_backend() == "gemini"


def test_router_disables_policy_backends_missing_from_providers():
    router = LLMRouter(
        backends={},
        providers={"openrouter_glm_5_2": DummyProvider("openrouter_glm_5_2")},
        default_backend="openrouter_glm_5_2",
        fallback_backend="gemini",
        multimodal_backend="gemini",
    )

    assert router.get_fallback_backend() is None
    assert router.get_multimodal_backend() is None


@pytest.mark.asyncio
async def test_router_routes_text_request_to_default_backend():
    glm = DummyProvider("openrouter_glm_5_2")
    gemini = DummyProvider("gemini")
    router = _make_router(glm, gemini)

    response = await router.send(LLMRequest(user_message="hi"))

    assert response.backend_name == "openrouter_glm_5_2"
    glm.send.assert_awaited_once()
    gemini.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_sends_attachments_to_multimodal_backend():
    glm = DummyProvider("openrouter_glm_5_2")
    gemini = DummyProvider("gemini")
    router = _make_router(glm, gemini)

    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": "이미지를 분석해줘",
                "attachments": [
                    MultimodalAttachment(
                        data=b"fake-image",
                        mime_type="image/png",
                        name="test.png",
                    )
                ],
            }
        ]
    )

    response = await router.send(request)

    assert response.backend_name == "gemini"
    gemini.send.assert_awaited_once()
    glm.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_explicit_backend_ignores_multimodal_routing():
    """explicit 지정은 첨부가 있어도 그대로 존중한다."""
    glm = DummyProvider("openrouter_glm_5_2")
    gemini = DummyProvider("gemini")
    router = _make_router(glm, gemini)

    request = LLMRequest(
        backend_name="openrouter_glm_5_2",
        messages=[
            {
                "role": "user",
                "content": "분석해줘",
                "attachments": [
                    MultimodalAttachment(data=b"x", mime_type="image/png")
                ],
            }
        ],
    )

    response = await router.send(request)

    assert response.backend_name == "openrouter_glm_5_2"
    gemini.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_falls_back_when_default_provider_raises():
    glm = DummyProvider("openrouter_glm_5_2")
    glm.send = AsyncMock(side_effect=LLMProviderError("boom"))
    gemini = DummyProvider("gemini", text="fallback ok")
    router = _make_router(glm, gemini)

    response = await router.send(LLMRequest(user_message="hi"))

    assert response.backend_name == "gemini"
    assert response.text == "fallback ok"
    glm.send.assert_awaited_once()
    gemini.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_falls_back_when_default_returns_empty_final():
    glm = DummyProvider("openrouter_glm_5_2", text="")
    gemini = DummyProvider("gemini", text="fallback text")
    router = _make_router(glm, gemini)

    response = await router.send(LLMRequest(user_message="hi"))

    assert response.backend_name == "gemini"
    assert response.text == "fallback text"


@pytest.mark.asyncio
async def test_router_does_not_fallback_when_default_returns_tool_calls():
    """텍스트가 비어도 tool_calls 가 있으면 empty final 이 아니다."""
    from simpleclaw.llm.models import ToolCall

    glm = DummyProvider("openrouter_glm_5_2")
    glm.send = AsyncMock(
        return_value=LLMResponse(
            text="",
            backend_name="openrouter_glm_5_2",
            model="m",
            tool_calls=[ToolCall(id="tc1", name="do_it", arguments={})],
        )
    )
    gemini = DummyProvider("gemini", text="fallback text")
    router = _make_router(glm, gemini)

    response = await router.send(LLMRequest(user_message="hi"))

    assert response.backend_name == "openrouter_glm_5_2"
    gemini.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_does_not_fallback_for_explicit_backend_empty_response():
    glm = DummyProvider("openrouter_glm_5_2", text="")
    gemini = DummyProvider("gemini", text="fallback text")
    router = _make_router(glm, gemini)

    response = await router.send(
        LLMRequest(user_message="hi", backend_name="openrouter_glm_5_2")
    )

    assert response.backend_name == "openrouter_glm_5_2"
    assert response.text == ""
    gemini.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_explicit_backend_provider_error_propagates():
    glm = DummyProvider("openrouter_glm_5_2")
    glm.send = AsyncMock(side_effect=LLMProviderError("boom"))
    gemini = DummyProvider("gemini", text="fallback ok")
    router = _make_router(glm, gemini)

    with pytest.raises(LLMProviderError):
        await router.send(
            LLMRequest(user_message="hi", backend_name="openrouter_glm_5_2")
        )
    gemini.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_streaming_does_not_fallback_on_error():
    """스트리밍 중 실패는 fallback 없이 그대로 전파 — 혼합 출력 방지."""
    glm = DummyProvider("openrouter_glm_5_2")
    glm.stream = AsyncMock(side_effect=LLMProviderError("boom"))
    gemini = DummyProvider("gemini", text="fallback ok")
    router = _make_router(glm, gemini)

    async def cb(_delta: str) -> None:
        pass

    with pytest.raises(LLMProviderError):
        await router.send(LLMRequest(user_message="hi"), on_text_delta=cb)
    gemini.send.assert_not_called()
    gemini.stream.assert_not_called()


@pytest.mark.asyncio
async def test_router_no_double_retry_when_fallback_equals_selected():
    """multimodal 라우팅 결과가 이미 fallback 백엔드면 재시도하지 않는다."""
    glm = DummyProvider("openrouter_glm_5_2")
    gemini = DummyProvider("gemini", text="")
    router = _make_router(glm, gemini)

    request = LLMRequest(
        messages=[
            {
                "role": "user",
                "content": "분석",
                "attachments": [
                    MultimodalAttachment(data=b"x", mime_type="image/png")
                ],
            }
        ]
    )

    response = await router.send(request)

    assert response.backend_name == "gemini"
    gemini.send.assert_awaited_once()
    glm.send.assert_not_called()


@pytest.mark.asyncio
async def test_router_passes_structured_kwargs_to_selected_backend():
    gemini = DummyProvider("gemini", text='{"ok":true}')
    router = LLMRouter(
        backends={},
        providers={"gemini": gemini},
        default_backend="gemini",
        fallback_backend=None,
        multimodal_backend="gemini",
    )
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    await router.send(
        LLMRequest(
            user_message="json",
            response_mime_type="application/json",
            response_schema=schema,
            require_structured_output=True,
        )
    )

    kwargs = gemini.send.call_args.kwargs
    assert kwargs["response_mime_type"] == "application/json"
    assert kwargs["response_schema"] == schema
    assert kwargs["require_structured_output"] is True
