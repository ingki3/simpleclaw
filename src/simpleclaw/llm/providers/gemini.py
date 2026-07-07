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
    MultimodalAttachment,
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

_GEMINI_INLINE_ATTACHMENT_MIME_TYPES = frozenset({
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/rtf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
})


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
    def _safe_attr(obj: object, name: str) -> object | None:
        """SDK 객체의 선택 속성을 안전하게 읽고 테스트 mock 기본값은 무시한다."""
        try:
            value = getattr(obj, name)
        except Exception:  # noqa: BLE001
            return None
        if value is None:
            return None
        if value.__class__.__module__.startswith("unittest.mock"):
            return None
        return value

    @classmethod
    def _stringify_diagnostic_value(cls, value: object | None) -> str | None:
        """Gemini enum/SDK 값을 로그 친화적인 문자열로 정규화한다."""
        if value is None:
            return None
        enum_name = cls._safe_attr(value, "name")
        if isinstance(enum_name, str):
            return enum_name
        text = str(value)
        if not text or text == "None":
            return None
        return text

    @classmethod
    def _extract_finish_diagnostics(
        cls,
        *,
        response: object | None,
        candidate: object | None,
        usage_metadata: object | None,
    ) -> tuple[str | None, dict | None]:
        """Gemini 종료 사유와 빈 출력 진단값을 provider-neutral dict로 추출한다."""
        finish_reason = cls._stringify_diagnostic_value(
            cls._safe_attr(candidate, "finish_reason")
            or cls._safe_attr(candidate, "finishReason")
        )
        prompt_feedback = cls._safe_attr(response, "prompt_feedback") if response else None
        block_reason = cls._stringify_diagnostic_value(
            cls._safe_attr(prompt_feedback, "block_reason")
            or cls._safe_attr(prompt_feedback, "blockReason")
        )
        diagnostics: dict[str, object] = {}
        if finish_reason:
            diagnostics["finish_reason"] = finish_reason
        if block_reason:
            diagnostics["block_reason"] = block_reason
        if usage_metadata is not None:
            prompt_tokens = cls._safe_attr(usage_metadata, "prompt_token_count")
            output_tokens = cls._safe_attr(usage_metadata, "candidates_token_count")
            if isinstance(prompt_tokens, int):
                diagnostics["prompt_token_count"] = prompt_tokens
            if isinstance(output_tokens, int):
                diagnostics["candidates_token_count"] = output_tokens
                diagnostics["empty_output_tokens"] = output_tokens == 0
        return finish_reason, diagnostics or None

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

    @staticmethod
    def _coerce_multimodal_attachment(raw: object) -> MultimodalAttachment | None:
        """dict/dataclass 첨부를 Gemini inline bytes attachment로 정규화한다."""
        if isinstance(raw, MultimodalAttachment):
            attachment = raw
        elif isinstance(raw, dict):
            data = raw.get("data")
            mime_type = raw.get("mime_type") or raw.get("mimeType")
            name = raw.get("name") or raw.get("file_name")
            path = raw.get("path")
            size_bytes = raw.get("size_bytes") or raw.get("sizeBytes")
            if data is None or not mime_type:
                return None
            attachment = MultimodalAttachment(
                data=bytes(data),
                mime_type=str(mime_type),
                name=name,
                path=path,
                size_bytes=size_bytes,
            )
        else:
            return None
        mime_type = attachment.mime_type.lower()
        if not attachment.data:
            return None
        if not (
            mime_type.startswith("image/")
            or mime_type in _GEMINI_INLINE_ATTACHMENT_MIME_TYPES
        ):
            return None
        return MultimodalAttachment(
            data=attachment.data,
            mime_type=mime_type,
            name=attachment.name,
            path=attachment.path,
            size_bytes=attachment.size_bytes,
        )

    @classmethod
    def _content_parts_for_message(cls, msg: dict) -> list[types.Part]:
        """텍스트 + 지원 첨부를 Gemini Content.parts 순서로 변환한다."""
        parts: list[types.Part] = []
        text = msg.get("content", "")
        if text:
            parts.append(types.Part(text=text))
        for raw_attachment in msg.get("attachments") or []:
            attachment = cls._coerce_multimodal_attachment(raw_attachment)
            if attachment is None:
                continue
            parts.append(
                types.Part.from_bytes(
                    data=attachment.data,
                    mime_type=attachment.mime_type,
                )
            )
        if not parts:
            parts.append(types.Part(text=""))
        return parts

    def _convert_messages(
        self, messages: list[dict]
    ) -> list[types.Content]:
        """generic 메시지 리스트를 Gemini Contents로 변환한다.

        assistant, user, tool 세 가지 role을 처리한다.
        tool result 메시지는 FunctionResponse로 변환하고,
        assistant의 tool_calls는 FunctionCall part로 변환한다. user 메시지에
        ``attachments``가 있으면 Gemini inline bytes 방식,
        즉 ``types.Part.from_bytes(data=..., mime_type=...)`` 로 지원 Part를 붙인다.
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
                # 일반 user/assistant 텍스트 메시지. user 메시지는 이미지 attachment를
                # inline bytes Part로 함께 전달한다.
                gemini_role = "model" if role == "assistant" else "user"
                contents.append(
                    types.Content(
                        role=gemini_role,
                        parts=self._content_parts_for_message(msg),
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
        max_tokens: int | None = None,
        response_mime_type: str | None = None,
        response_schema: dict | type | None = None,
        require_structured_output: bool = False,
    ) -> LLMResponse:
        """Gemini API로 메시지를 전송하고 응답을 반환한다.

        BIZ-427 — ``response_mime_type``/``response_schema`` 가 주어지면 Gemini
        네이티브 structured output(``GenerateContentConfig.response_mime_type`` /
        ``response_schema``)으로 매핑해 schema-valid JSON 출력을 강제한다.
        """
        # required 인데 두 힌트가 온전히 오지 않으면 빠르게 실패 — 반쪽 설정으로
        # 프롬프트-only JSON 이 조용히 나가는 사고를 막는다.
        if require_structured_output and not (
            response_mime_type and response_schema is not None
        ):
            raise LLMProviderError(
                "Gemini structured output requires both response_mime_type "
                "and response_schema"
            )
        # BIZ-252 — Gemini 는 prompt caching 마커가 없는 단일 문자열만 받으므로
        # system_blocks 가 있으면 텍스트만 이어 붙인다.
        effective_system = flatten_system_blocks(system_blocks, fallback=system_prompt)
        try:
            config = types.GenerateContentConfig(
                system_instruction=effective_system if effective_system else None,
            )
            # BIZ-297 — max_tokens 가 지정되면 출력 토큰 cap 으로 사용. None 이면
            # max_output_tokens 를 비워 두고 모델 기본값에 맡긴다 (회귀 0).
            if max_tokens:
                config.max_output_tokens = max_tokens

            # BIZ-427 — structured output 매핑. 미지정이면 기존 동작 그대로.
            if response_mime_type:
                config.response_mime_type = response_mime_type
            if response_schema is not None:
                config.response_schema = response_schema

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
        candidate = None

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
        usage_metadata = response.usage_metadata if response and response.usage_metadata else None
        if usage_metadata:
            usage = {
                "input_tokens": usage_metadata.prompt_token_count or 0,
                "output_tokens": usage_metadata.candidates_token_count or 0,
            }
        finish_reason, diagnostics = self._extract_finish_diagnostics(
            response=response,
            candidate=candidate,
            usage_metadata=usage_metadata,
        )

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls,
            raw_assistant_message=raw_content,
            finish_reason=finish_reason,
            diagnostics=diagnostics,
        )

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools: list[ToolDefinition] | None = None,
        system_blocks: list[SystemBlock] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        max_tokens: int | None = None,
        response_mime_type: str | None = None,
        response_schema: dict | type | None = None,
        require_structured_output: bool = False,
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

        BIZ-427 — SimpleClaw 에서 structured output 은 비스트리밍 send() 전용이다.
        required 스트리밍 요청은 명확히 거부하고, required 가 아니면 힌트를
        무시한다 — 스트리밍 tool loop 에 schema 가 오적용되는 사고 방지.
        BIZ-430 — 거부는 힌트 유무가 아니라 required 플래그만으로 결정한다.
        """
        if require_structured_output:
            raise LLMProviderError(
                "Gemini structured output is only supported on non-streaming "
                "send() in SimpleClaw"
            )
        effective_system = flatten_system_blocks(system_blocks, fallback=system_prompt)

        config = types.GenerateContentConfig(
            system_instruction=effective_system if effective_system else None,
        )
        # BIZ-297 — stream() 도 send() 와 동일하게 max_tokens 를 그대로 매핑.
        if max_tokens:
            config.max_output_tokens = max_tokens
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
        last_candidate = None  # 종료 사유/진단값 추출용 마지막 candidate
        last_chunk = None  # prompt_feedback 등 응답 단위 진단값 추출용

        try:
            stream_iter = await self._client.aio.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=config,
            )
            async for chunk in stream_iter:
                last_chunk = chunk
                if chunk.candidates:
                    candidate = chunk.candidates[0]
                    last_candidate = candidate
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
        finish_reason, diagnostics = self._extract_finish_diagnostics(
            response=last_chunk,
            candidate=last_candidate,
            usage_metadata=usage_metadata,
        )

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls,
            raw_assistant_message=raw_content,
            finish_reason=finish_reason,
            diagnostics=diagnostics,
        )
