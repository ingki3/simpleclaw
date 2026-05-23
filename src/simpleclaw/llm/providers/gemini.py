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
    SystemBlock,
    ToolCall,
    ToolDefinition,
)
from simpleclaw.llm.providers.base import (
    LLMProvider,
    TextDeltaCallback,
    flatten_system_blocks,
)

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
                # 도구 실행 결과 → FunctionResponse. BIZ-249: Gemini 3.5 부터 동일
                # FunctionCall.id 와의 매칭이 필수이므로 orchestrator 가 박아 둔
                # ``tool_call_id`` 를 그대로 옮긴다. 없으면 None (구버전 호환).
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=msg.get("tool_call_id") or None,
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
                    # fallback: raw가 없으면 수동 구성. BIZ-249 — FunctionCall.id 도
                    # 같이 보존해 다음 턴 FunctionResponse.id 와 매칭되게 한다.
                    parts = []
                    for tc in msg["tool_calls"]:
                        parts.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    id=tc.get("id") or None,
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
        system_blocks: list[SystemBlock] | None = None,
    ) -> LLMResponse:
        """Gemini API로 메시지를 전송하고 응답을 반환한다."""
        # BIZ-252 — Gemini 는 prompt caching 마커가 없는 단일 문자열만 받으므로
        # system_blocks 가 있으면 텍스트만 이어 붙인다.
        effective_system = flatten_system_blocks(system_blocks, fallback=system_prompt)
        try:
            config = types.GenerateContentConfig(
                system_instruction=effective_system if effective_system else None,
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
                        # BIZ-249 — Gemini 3.5 부터 FunctionResponse.id 가 FunctionCall.id
                        # 와 매칭되어야 하므로, 모델이 반환한 id 가 있으면 그대로 보존하고
                        # 없을 때만 fallback uuid 를 발급한다.
                        fc_id = getattr(fc, "id", None) or str(uuid.uuid4())
                        fc_list.append(
                            ToolCall(
                                id=fc_id,
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

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools: list[ToolDefinition] | None = None,
        system_blocks: list[SystemBlock] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """Gemini API streaming — text 델타를 ``on_text_delta`` 로 흘린다.

        BIZ-284: ``client.aio.models.generate_content_stream`` async iterator 를
        받아 각 청크의 ``candidates[0].content.parts`` 를 순회한다. ``part.text``
        델타마다 콜백을 await 하고, ``part.function_call`` 은 청크 단위로 누적해
        최종 LLMResponse 의 tool_calls 로 반환한다. ``raw_assistant_message`` 는
        마지막 청크의 ``candidate.content`` 를 보존 — 다음 턴에서
        ``thought_signature`` 가 살아 있어야 Gemini 3.x 멀티턴이 깨지지 않는다.

        스트림 중 콜백 예외는 흡수해 누적은 완수한다 (Claude 와 동일 정책).
        ``on_text_delta=None`` 이면 그냥 누적만 해서 send() 와 동일 결과 — 호출
        측 통일 인터페이스.
        """
        effective_system = flatten_system_blocks(system_blocks, fallback=system_prompt)

        config = types.GenerateContentConfig(
            system_instruction=effective_system if effective_system else None,
        )
        if tools:
            config.tools = self._convert_tools(tools)

        if messages is not None:
            contents = self._convert_messages(messages)
        else:
            contents = user_message

        text_parts: list[str] = []
        fc_parts: list[object] = []  # 누적된 raw FunctionCall (마지막 청크까지)
        last_content = None  # raw_assistant_message 보존용 (thought_signature)
        usage_metadata = None  # 종료 청크의 usage_metadata

        try:
            stream_iter = await self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=config,
            )
            async for chunk in stream_iter:
                if chunk.candidates:
                    candidate = chunk.candidates[0]
                    if candidate.content is not None:
                        # 매 청크의 content 를 갱신 — 마지막 청크가 최종 raw.
                        last_content = candidate.content
                        if candidate.content.parts:
                            for part in candidate.content.parts:
                                if part.function_call is not None:
                                    fc_parts.append(part.function_call)
                                    continue
                                delta_text = part.text or ""
                                if not delta_text:
                                    continue
                                text_parts.append(delta_text)
                                if on_text_delta is not None:
                                    try:
                                        await on_text_delta(delta_text)
                                    except Exception as exc:  # noqa: BLE001
                                        # sink 측 일시 오류(텔레그램 rate limit 등) 가
                                        # LLM 응답 자체를 깨뜨리지 않도록 흡수.
                                        logger.warning(
                                            "Gemini stream on_text_delta callback "
                                            "raised: %s",
                                            exc,
                                        )
                if chunk.usage_metadata is not None:
                    usage_metadata = chunk.usage_metadata
        except Exception as e:
            # send() 와 동일한 매핑 — 통합 에러 계층이 없으므로 이름 기반 분기.
            error_name = type(e).__name__
            if "auth" in error_name.lower() or "permission" in error_name.lower():
                raise LLMAuthError(f"Gemini auth failed: {e}") from e
            raise LLMProviderError(f"Gemini API error: {e}") from e

        text = "".join(text_parts)

        tool_calls: list[ToolCall] | None = None
        raw_content = None
        if fc_parts:
            tc_list: list[ToolCall] = []
            for fc in fc_parts:
                # BIZ-249 — 모델이 반환한 fc.id 가 있으면 그대로 보존, 없으면 fallback.
                fc_id = getattr(fc, "id", None) or str(uuid.uuid4())
                tc_list.append(
                    ToolCall(
                        id=fc_id,
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
                )
            tool_calls = tc_list
            # tool call 이 있으면 thought_signature 보존을 위해 마지막 content 를 노출.
            raw_content = last_content

        usage = None
        if usage_metadata is not None:
            usage = {
                "input_tokens": usage_metadata.prompt_token_count or 0,
                "output_tokens": usage_metadata.candidates_token_count or 0,
            }

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls,
            raw_assistant_message=raw_content,
        )
