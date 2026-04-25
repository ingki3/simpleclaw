"""Anthropic Claude API 프로바이더.

Anthropic의 Messages API를 사용하여 Claude 모델과 통신한다.
시스템 프롬프트는 별도의 system 파라미터로 전달되며,
멀티턴 대화는 messages 리스트를 그대로 API에 전달한다.
"""

from __future__ import annotations

import logging

import anthropic

from simpleclaw.llm.models import LLMAuthError, LLMProviderError, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    """Anthropic Claude API 프로바이더."""

    def __init__(self, model: str, api_key: str, name: str = "claude") -> None:
        """ClaudeProvider를 초기화한다.

        Args:
            model: 사용할 Claude 모델 ID (예: claude-sonnet-4-20250514).
            api_key: Anthropic API 키.
            name: 라우터에서 이 백엔드를 식별하는 이름.

        Raises:
            LLMAuthError: API 키가 비어있는 경우.
        """
        if not api_key:
            raise LLMAuthError(f"API key missing for provider '{name}' (env var not set)")
        self._model = model
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._name = name

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
    ) -> LLMResponse:
        """Claude Messages API로 메시지를 전송하고 응답을 반환한다."""
        if messages is not None:
            msg_list = messages
        else:
            msg_list = [{"role": "user", "content": user_message}]

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                # 시스템 프롬프트가 없으면 NOT_GIVEN으로 파라미터 자체를 생략
                system=system_prompt if system_prompt else anthropic.NOT_GIVEN,
                messages=msg_list,
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
