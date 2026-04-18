"""OpenAI ChatGPT LLM provider."""

from __future__ import annotations

import logging

import openai

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI ChatGPT API."""

    def __init__(self, model: str, api_key: str, name: str = "openai") -> None:
        if not api_key:
            raise LLMAuthError(f"API key missing for provider '{name}' (env var not set)")
        self._model = model
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self._name = name

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
    ) -> LLMResponse:
        msg_list = []
        if system_prompt:
            msg_list.append({"role": "system", "content": system_prompt})
        if messages is not None:
            msg_list.extend(messages)
        else:
            msg_list.append({"role": "user", "content": user_message})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=msg_list,
            )
        except openai.AuthenticationError as e:
            raise LLMAuthError(f"OpenAI auth failed: {e}") from e
        except openai.APIError as e:
            raise LLMProviderError(f"OpenAI API error: {e}") from e

        choice = response.choices[0] if response.choices else None
        text = choice.message.content or "" if choice else ""
        usage = None
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
        )
