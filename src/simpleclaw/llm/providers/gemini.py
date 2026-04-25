"""Google Gemini API 프로바이더.

Google의 genai SDK를 사용하여 Gemini 모델과 통신한다.
시스템 프롬프트는 GenerateContentConfig의 system_instruction으로 전달되며,
멀티턴 대화 시 role 매핑(assistant → model)을 수행한다.
"""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Google Gemini API 프로바이더."""

    def __init__(self, model: str, api_key: str, name: str = "gemini") -> None:
        """GeminiProvider를 초기화한다.

        Args:
            model: 사용할 Gemini 모델 ID (예: gemini-2.0-flash).
            api_key: Google AI API 키.
            name: 라우터에서 이 백엔드를 식별하는 이름.

        Raises:
            LLMAuthError: API 키가 비어있는 경우.
        """
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
        """Gemini API로 메시지를 전송하고 응답을 반환한다."""
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
            )

            if messages is not None:
                # Gemini는 assistant 대신 model을 role로 사용하므로 변환 필요
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
            # Gemini SDK는 통합된 에러 계층이 없으므로 이름 기반으로 인증 에러를 판별
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
