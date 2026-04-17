"""Tests for LLM providers with mocked SDK calls."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.llm.models import LLMAuthError, LLMProviderError
from simpleclaw.llm.providers.claude import ClaudeProvider
from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.providers.gemini import GeminiProvider


class TestClaudeProvider:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMAuthError):
            ClaudeProvider(model="claude-sonnet-4-20250514", api_key="")

    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        provider = ClaudeProvider(
            model="claude-sonnet-4-20250514", api_key="test-key"
        )

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Hello from Claude")]
        mock_message.usage = MagicMock(input_tokens=10, output_tokens=5)

        provider._client.messages.create = AsyncMock(return_value=mock_message)

        result = await provider.send("system prompt", "hello")
        assert result.text == "Hello from Claude"
        assert result.backend_name == "claude"
        assert result.usage["input_tokens"] == 10

    @pytest.mark.asyncio
    async def test_auth_error(self):
        import anthropic
        provider = ClaudeProvider(model="test", api_key="bad-key")
        provider._client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body={"error": {"message": "Invalid API key"}},
            )
        )
        with pytest.raises(LLMAuthError):
            await provider.send("sys", "msg")


class TestOpenAIProvider:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMAuthError):
            OpenAIProvider(model="gpt-4o", api_key="")

    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        provider = OpenAIProvider(model="gpt-4o", api_key="test-key")

        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from OpenAI"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=8, completion_tokens=4)

        provider._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await provider.send("system", "hello")
        assert result.text == "Hello from OpenAI"
        assert result.backend_name == "openai"


class TestGeminiProvider:
    def test_missing_api_key_raises(self):
        with pytest.raises(LLMAuthError):
            GeminiProvider(model="gemini-2.0-flash", api_key="")

    @pytest.mark.asyncio
    async def test_send_returns_response(self):
        provider = GeminiProvider(model="gemini-2.0-flash", api_key="test-key")

        mock_response = MagicMock()
        mock_response.text = "Hello from Gemini"
        mock_response.usage_metadata = MagicMock(
            prompt_token_count=7, candidates_token_count=3
        )

        provider._client.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        result = await provider.send("system", "hello")
        assert result.text == "Hello from Gemini"
        assert result.backend_name == "gemini"
