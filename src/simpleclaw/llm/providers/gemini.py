"""Google Gemini API 프로바이더 — Native Function Calling 지원.

Google의 genai SDK를 사용하여 Gemini 모델과 통신한다.
시스템 프롬프트는 GenerateContentConfig의 system_instruction으로 전달되며,
멀티턴 대화 시 role 매핑(assistant → model)을 수행한다.

Function Calling:
  tools가 주어지면 ToolDefinition을 Gemini의 FunctionDeclaration으로 변환하여
  API에 전달하고, 응답에서 FunctionCall part를 감지하여 ToolCall로 반환한다.
  도구 결과 메시지(role=tool)는 FunctionResponse로 변환하여 멀티턴에 포함한다.
"""

from __future__ import annotations

import logging
import uuid

from google import genai
from google.genai import types

from simpleclaw.llm.models import (
    LLMAuthError,
    LLMProviderError,
    LLMResponse,
    ToolCall,
    ToolDefinition,
)
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

    @staticmethod
    def _convert_tools(
        tools: list[ToolDefinition],
    ) -> list[types.Tool]:
        """ToolDefinition 리스트를 Gemini SDK의 Tool 객체로 변환한다."""
        declarations = []
        for td in tools:
            declarations.append(
                types.FunctionDeclaration(
                    name=td.name,
                    description=td.description,
                    parameters=td.parameters if td.parameters else None,
                )
            )
        return [types.Tool(function_declarations=declarations)]

    def _convert_messages(
        self, messages: list[dict]
    ) -> list[types.Content]:
        """generic 메시지 리스트를 Gemini Contents로 변환한다.

        assistant, user, tool 세 가지 role을 처리한다.
        tool result 메시지는 FunctionResponse로 변환하고,
        assistant의 tool_calls는 FunctionCall part로 변환한다.
        """
        contents: list[types.Content] = []
        for msg in messages:
            role = msg.get("role", "user")

            if role == "tool":
                # 도구 실행 결과 → FunctionResponse
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=msg.get("name", ""),
                                    response={"result": msg.get("content", "")},
                                )
                            )
                        ],
                    )
                )
            elif role == "assistant" and msg.get("tool_calls"):
                # _raw_content가 있으면 thought_signature 등을 보존하여 그대로 사용
                raw = msg.get("_raw_content")
                if raw is not None:
                    contents.append(raw)
                else:
                    # fallback: raw가 없으면 수동 구성
                    parts = []
                    for tc in msg["tool_calls"]:
                        parts.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    name=tc["name"],
                                    args=tc.get("arguments", {}),
                                )
                            )
                        )
                    if msg.get("content"):
                        parts.insert(0, types.Part(text=msg["content"]))
                    contents.append(types.Content(role="model", parts=parts))
            else:
                # 일반 user/assistant 텍스트 메시지
                gemini_role = "model" if role == "assistant" else "user"
                contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=[types.Part(text=msg.get("content", ""))],
                    )
                )
        return contents

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMResponse:
        """Gemini API로 메시지를 전송하고 응답을 반환한다."""
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt if system_prompt else None,
            )

            # 도구 정의가 있으면 config에 추가
            if tools:
                config.tools = self._convert_tools(tools)

            if messages is not None:
                contents = self._convert_messages(messages)
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

        # 응답에서 tool_calls와 text 추출
        text = ""
        tool_calls: list[ToolCall] | None = None
        raw_content = None  # thought_signature 보존용 원본 Content

        if response and response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                fc_list = []
                text_parts = []
                for part in candidate.content.parts:
                    if part.function_call:
                        fc = part.function_call
                        fc_list.append(
                            ToolCall(
                                id=str(uuid.uuid4()),
                                name=fc.name,
                                arguments=dict(fc.args) if fc.args else {},
                            )
                        )
                    elif part.text:
                        text_parts.append(part.text)
                if fc_list:
                    tool_calls = fc_list
                    # tool call이 있으면 원본 Content 보존 (thought_signature 포함)
                    raw_content = candidate.content
                if text_parts:
                    text = "\n".join(text_parts)

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
            tool_calls=tool_calls,
            raw_assistant_message=raw_content,
        )
