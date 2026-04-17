"""Anthropic Claude LLM provider."""

from __future__ import annotations

import logging

import anthropic

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    """Provider for Anthropic Claude API."""

    def __init__(self, model: str, api_key: str, name: str = "claude") -> None:
        if not api_key:
            raise LLMAuthError(f"API key missing for provider '{name}' (env var not set)")
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._name = name

    async def send(self, system_prompt: str, user_message: str) -> LLMResponse:
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.AuthenticationError as e:
            raise LLMAuthError(f"Claude auth failed: {e}") from e
        except anthropic.APIError as e:
            raise LLMProviderError(f"Claude API error: {e}") from e

        text = message.content[0].text if message.content else ""
        usage = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        }

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
        )
