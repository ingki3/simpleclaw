"""OpenAI ChatGPT API 프로바이더 — Native Function Calling 지원.

OpenAI의 Chat Completions API를 사용하여 GPT 모델과 통신한다.
시스템 프롬프트는 role=system 메시지로 messages 리스트 맨 앞에 삽입되며,
멀티턴 대화는 기존 messages에 시스템 프롬프트를 선행 추가하여 전달한다.

Function Calling:
  tools가 주어지면 ToolDefinition을 OpenAI의 function tool 형식으로 변환하여
  API에 전달하고, 응답에서 tool_calls를 감지하여 ToolCall로 반환한다.
  도구 결과 메시지(role=tool)는 OpenAI 네이티브 형식과 동일하여 변환 불필요.
"""

from __future__ import annotations

import json
import logging

import openai

from simpleclaw.llm.models import (
    LLMAuthError,
    LLMProviderError,
    LLMResponse,
    ToolCall,
    ToolDefinition,
)
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

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict]:
        """ToolDefinition 리스트를 OpenAI의 function tool 형식으로 변환한다."""
        return [
            {
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.parameters if td.parameters else {"type": "object", "properties": {}},
                },
            }
            for td in tools
        ]

    @staticmethod
    def _convert_messages(messages: list[dict]) -> list[dict]:
        """generic 메시지 리스트를 OpenAI Chat Completions 형식으로 변환한다.

        assistant의 tool_calls는 OpenAI 네이티브 형식으로 변환하고,
        tool result는 그대로 사용한다 (OpenAI 네이티브 형식과 동일).
        """
        result = []
        for msg in messages:
            role = msg.get("role", "user")

            if role == "tool":
                # OpenAI는 {"role": "tool", "tool_call_id": ..., "content": ...} 그대로 사용
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
            elif role == "assistant" and msg.get("tool_calls"):
                # assistant의 도구 호출 → OpenAI tool_calls 형식
                openai_tcs = []
                for tc in msg["tool_calls"]:
                    openai_tcs.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    })
                result.append({
                    "role": "assistant",
                    "content": msg.get("content", "") or None,
                    "tool_calls": openai_tcs,
                })
            else:
                result.append({"role": role, "content": msg.get("content", "")})

        return result

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Chat Completions API로 메시지를 전송하고 응답을 반환한다."""
        # 시스템 프롬프트를 맨 앞에 배치한 뒤 대화 메시지를 이어붙임
        msg_list: list[dict] = []
        if system_prompt:
            msg_list.append({"role": "system", "content": system_prompt})
        if messages is not None:
            msg_list.extend(self._convert_messages(messages))
        else:
            msg_list.append({"role": "user", "content": user_message})

        try:
            kwargs: dict = {
                "model": self._model,
                "messages": msg_list,
            }
            if tools:
                kwargs["tools"] = self._convert_tools(tools)

            response = await self._client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise LLMAuthError(f"OpenAI auth failed: {e}") from e
        except openai.APIError as e:
            raise LLMProviderError(f"OpenAI API error: {e}") from e

        choice = response.choices[0] if response.choices else None
        text = choice.message.content or "" if choice else ""

        # tool_calls 추출
        tool_calls: list[ToolCall] | None = None
        if choice and choice.message.tool_calls:
            tc_list = []
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tc_list.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )
            if tc_list:
                tool_calls = tc_list

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
            tool_calls=tool_calls,
        )
