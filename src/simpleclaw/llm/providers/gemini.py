"""Google Gemini LLM provider."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Provider for Google Gemini API."""

    def __init__(self, model: str, api_key: str, name: str = "gemini") -> None:
        if not api_key:
            raise LLMAuthError(f"API key missing for provider '{name}' (env var not set)")
        self._model = model
        self._client = genai.Client(api_key=api_key)
        self._name = name

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
    ) -> LLMResponse:
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
            )

            if messages is not None:
                # Convert to Gemini contents format
                contents = []
                for msg in messages:
                    role = "model" if msg["role"] == "assistant" else "user"
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[types.Part(text=msg["content"])],
                        )
                    )
            else:
                contents = user_message

            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            error_name = type(e).__name__
            if "auth" in error_name.lower() or "permission" in error_name.lower():
                raise LLMAuthError(f"Gemini auth failed: {e}") from e
            raise LLMProviderError(f"Gemini API error: {e}") from e

        text = response.text or "" if response else ""
        usage = None
        if response and response.usage_metadata:
            usage = {
                "input_tokens": response.usage_metadata.prompt_token_count or 0,
                "output_tokens": response.usage_metadata.candidates_token_count or 0,
            }

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
        )
