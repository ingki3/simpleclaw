"""Tests for the LLM router."""

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.models import LLMConfigError, LLMRequest, LLMResponse
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

    async def send(self, system_prompt: str, user_message: str, messages: list[dict] | None = None) -> LLMResponse:
        return await self._mock_send(system_prompt, user_message, messages)


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
            "You are helpful.", "hello", None
        )
