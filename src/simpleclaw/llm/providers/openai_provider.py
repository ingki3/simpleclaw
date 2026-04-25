"""OpenAI ChatGPT API 프로바이더.

OpenAI의 Chat Completions API를 사용하여 GPT 모델과 통신한다.
시스템 프롬프트는 role=system 메시지로 messages 리스트 맨 앞에 삽입되며,
멀티턴 대화는 기존 messages에 시스템 프롬프트를 선행 추가하여 전달한다.
"""

from __future__ import annotations

import logging

import openai

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI ChatGPT API 프로바이더."""

    def __init__(self, model: str, api_key: str, name: str = "openai") -> None:
        """OpenAIProvider를 초기화한다.

        Args:
            model: 사용할 OpenAI 모델 ID (예: gpt-4o).
            api_key: OpenAI API 키.
            name: 라우터에서 이 백엔드를 식별하는 이름.

        Raises:
            LLMAuthError: API 키가 비어있는 경우.
        """
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
        """Chat Completions API로 메시지를 전송하고 응답을 반환한다."""
        # 시스템 프롬프트를 맨 앞에 배치한 뒤 대화 메시지를 이어붙임
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
