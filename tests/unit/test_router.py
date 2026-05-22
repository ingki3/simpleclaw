"""Tests for the LLM router."""

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.models import LLMConfigError, LLMRequest, LLMResponse, SystemBlock
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter


class MockProvider(LLMProvider):
    """A mock provider for testing."""

    def __init__(self, name: str = "mock"):
        self._name = name
        self._mock_send = AsyncMock(
            return_value=LLMResponse(
                text=f"Response from {name}",
                backend_name=name,
                model="mock-model",
            )
        )

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools=None,
        system_blocks=None,
    ) -> LLMResponse:
        return await self._mock_send(
            system_prompt, user_message, messages, tools, system_blocks
        )


class TestLLMRouter:
    @pytest.fixture
    def router(self):
        providers = {
            "provider_a": MockProvider("provider_a"),
            "provider_b": MockProvider("provider_b"),
        }
        return LLMRouter(
            backends={},
            providers=providers,
            default_backend="provider_a",
        )

    @pytest.mark.asyncio
    async def test_default_backend(self, router):
        request = LLMRequest(user_message="hello")
        response = await router.send(request)
        assert response.backend_name == "provider_a"

    @pytest.mark.asyncio
    async def test_explicit_backend(self, router):
        request = LLMRequest(user_message="hello", backend_name="provider_b")
        response = await router.send(request)
        assert response.backend_name == "provider_b"

    @pytest.mark.asyncio
    async def test_unknown_backend_raises(self, router):
        request = LLMRequest(user_message="hello", backend_name="nonexistent")
        with pytest.raises(LLMConfigError, match="Unknown backend"):
            await router.send(request)

    def test_list_backends(self, router):
        backends = router.list_backends()
        assert "provider_a" in backends
        assert "provider_b" in backends
        assert len(backends) == 2

    def test_get_default_backend(self, router):
        assert router.get_default_backend() == "provider_a"

    @pytest.mark.asyncio
    async def test_system_prompt_passed(self, router):
        request = LLMRequest(
            system_prompt="You are helpful.",
            user_message="hello",
        )
        await router.send(request)
        router._providers["provider_a"]._mock_send.assert_called_once_with(
            "You are helpful.", "hello", None, None, None
        )

    @pytest.mark.asyncio
    async def test_system_blocks_passed_through(self, router):
        """BIZ-252 — LLMRequest.system_blocks 가 프로바이더 send() 까지 전달되어야
        Anthropic prompt caching 마커가 부착될 수 있다."""
        blocks = [SystemBlock(text="persona", cache=True)]
        request = LLMRequest(
            system_prompt="legacy fallback",
            user_message="hello",
            system_blocks=blocks,
        )
        await router.send(request)
        router._providers["provider_a"]._mock_send.assert_called_once_with(
            "legacy fallback", "hello", None, None, blocks
        )

    @pytest.mark.asyncio
    async def test_send_without_callback_does_not_invoke_stream(self, router):
        """BIZ-259 — on_text_delta 미지정 시 send() 경로 유지 (회귀 0)."""
        provider = router._providers["provider_a"]
        # MockProvider 에는 stream() 오버라이드가 없으므로 호출 여부 확인을 위해 spy 부착.
        provider._mock_stream_called = False
        original_stream = provider.stream

        async def spy_stream(*args, **kwargs):
            provider._mock_stream_called = True
            return await original_stream(*args, **kwargs)

        provider.stream = spy_stream  # type: ignore[assignment]
        request = LLMRequest(user_message="hi")
        await router.send(request)
        assert provider._mock_stream_called is False

    @pytest.mark.asyncio
    async def test_send_with_callback_routes_to_provider_stream(self, router):
        """BIZ-259 — on_text_delta 지정 시 provider.stream() 으로 라우팅."""
        collected: list[str] = []

        async def cb(delta: str) -> None:
            collected.append(delta)

        # MockProvider 의 send() 가 "Response from provider_a" 텍스트를 돌려주므로
        # base 의 fallback stream() 이 그대로 콜백으로 흘려보낸다.
        request = LLMRequest(user_message="hi")
        await router.send(request, on_text_delta=cb)
        assert collected == ["Response from provider_a"]
