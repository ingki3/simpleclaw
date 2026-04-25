"""Anthropic Claude API 프로바이더 — Native Function Calling (Tool Use) 지원.

Anthropic의 Messages API를 사용하여 Claude 모델과 통신한다.
시스템 프롬프트는 별도의 system 파라미터로 전달되며,
멀티턴 대화는 messages 리스트를 그대로 API에 전달한다.

Tool Use:
  tools가 주어지면 ToolDefinition을 Claude의 ToolParam으로 변환하여
  API에 전달하고, 응답에서 ToolUseBlock을 감지하여 ToolCall로 반환한다.
  도구 결과 메시지(role=tool)는 ToolResultBlockParam으로 변환한다.
"""

from __future__ import annotations

import json
import logging

import anthropic

from simpleclaw.llm.models import (
    LLMAuthError,
    LLMProviderError,
    LLMResponse,
    ToolCall,
    ToolDefinition,
)
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

    @staticmethod
    def _convert_tools(tools: list[ToolDefinition]) -> list[dict]:
        """ToolDefinition 리스트를 Claude의 ToolParam 형식으로 변환한다."""
        return [
            {
                "name": td.name,
                "description": td.description,
                "input_schema": td.parameters if td.parameters else {"type": "object", "properties": {}},
            }
            for td in tools
        ]

    @staticmethod
    def _convert_messages(messages: list[dict]) -> list[dict]:
        """generic 메시지 리스트를 Claude Messages API 형식으로 변환한다.

        tool result 메시지는 ToolResultBlockParam content로 변환하고,
        assistant의 tool_calls는 ToolUseBlock content로 변환한다.
        """
        result = []
        for msg in messages:
            role = msg.get("role", "user")

            if role == "tool":
                # 도구 결과 → user 메시지에 tool_result content block
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": msg.get("content", ""),
                        }
                    ],
                })
            elif role == "assistant" and msg.get("tool_calls"):
                # assistant의 도구 호출 → tool_use content blocks
                content = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("arguments", {}),
                    })
                result.append({"role": "assistant", "content": content})
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
        """Claude Messages API로 메시지를 전송하고 응답을 반환한다."""
        if messages is not None:
            msg_list = self._convert_messages(messages)
        else:
            msg_list = [{"role": "user", "content": user_message}]

        try:
            kwargs: dict = {
                "model": self._model,
                "max_tokens": 4096,
                "messages": msg_list,
            }
            # 시스템 프롬프트가 없으면 파라미터 자체를 생략
            if system_prompt:
                kwargs["system"] = system_prompt
            # 도구 정의가 있으면 추가
            if tools:
                kwargs["tools"] = self._convert_tools(tools)

            message = await self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMAuthError(f"Claude auth failed: {e}") from e
        except anthropic.APIError as e:
            raise LLMProviderError(f"Claude API error: {e}") from e

        # 응답에서 text와 tool_calls 추출
        text = ""
        tool_calls: list[ToolCall] | None = None

        if message.content:
            text_parts = []
            tc_list = []
            for block in message.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tc_list.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=block.input if isinstance(block.input, dict) else {},
                        )
                    )
            if text_parts:
                text = "\n".join(text_parts)
            if tc_list:
                tool_calls = tc_list

        usage = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        }

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls,
        )
