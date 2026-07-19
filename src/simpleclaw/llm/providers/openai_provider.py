"""OpenAI ChatGPT API 프로바이더 — Native Function Calling 지원.

OpenAI의 Chat Completions API를 사용하여 GPT 모델과 통신한다.
시스템 프롬프트는 role=system 메시지로 messages 리스트 맨 앞에 삽입되며,
멀티턴 대화는 기존 messages에 시스템 프롬프트를 선행 추가하여 전달한다.

Function Calling:
  tools가 주어지면 ToolDefinition을 OpenAI의 function tool 형식으로 변환하여
  API에 전달하고, 응답에서 tool_calls를 감지하여 ToolCall로 반환한다.
  도구 결과 메시지(role=tool)는 OpenAI 네이티브 형식과 동일하여 변환 불필요.

Structured Output (BIZ-450):
  provider-neutral 힌트(response_mime_type/response_schema/require_structured_output)
  를 Chat Completions ``response_format`` 으로 매핑한다. dict JSON Schema 는
  ``json_schema`` (strict), schema 없는 optional JSON 힌트는 ``json_object`` 로
  변환하며, Gemini 전용 확장 키(propertyOrdering)는 전송 전에 제거한다.
"""

from __future__ import annotations

import json
import logging

import openai

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


def _max_tokens_field(model: str) -> str:
    """OpenAI 모델군에 따라 출력 cap 필드 이름을 결정한다 (BIZ-297).

    o1/o3 reasoning 모델은 ``max_completion_tokens`` 를 요구하며 ``max_tokens`` 는
    400 으로 거절된다. 그 외(gpt-4o, gpt-4, gpt-3.5 등) 는 ``max_tokens`` 를 사용.
    호출자가 모델별 분기를 신경 쓰지 않도록 프로바이더 안에서 흡수한다.
    """
    name = (model or "").lower()
    if name.startswith(("o1", "o3")):
        return "max_completion_tokens"
    return "max_tokens"


# BIZ-450 — provider-neutral schema 에 섞여 들어오는 Gemini 전용 확장 키.
# OpenAI/OpenRouter json_schema 모드는 더 엄격한 JSON Schema subset 만 받으므로
# 검증 계약은 보존하면서 확장 키만 재귀 제거한다.
_OPENAI_SCHEMA_DROP_KEYS = frozenset({"propertyOrdering"})


def _sanitize_json_schema_for_openai(schema: object) -> object:
    """OpenAI/OpenRouter 호출 전 provider 전용 JSON Schema 확장 키를 제거한다."""
    if isinstance(schema, dict):
        return {
            key: _sanitize_json_schema_for_openai(value)
            for key, value in schema.items()
            if key not in _OPENAI_SCHEMA_DROP_KEYS
        }
    if isinstance(schema, list):
        return [_sanitize_json_schema_for_openai(item) for item in schema]
    return schema


def _openai_response_format(
    *,
    provider_name: str,
    response_mime_type: str | None,
    response_schema: dict | type | None,
    require_structured_output: bool,
) -> dict | None:
    """SimpleClaw structured output 힌트를 Chat Completions ``response_format`` 으로 매핑한다.

    ``require_structured_output=True`` 는 best-effort 힌트가 아니라 보장 계약이다
    (BIZ-430). dict JSON Schema 가 있으면 ``response_format.type=json_schema`` 로
    보장할 수 있지만, schema 없이 JSON 문법만 강제하는 것으로는 required 계약을
    만족할 수 없으므로 API 호출 전에 빠르게 실패한다 — 잘못된 JSON 을 조용히
    돌려주는 것보다 호출자가 fallback 을 결정하게 하는 편이 안전하다.
    """
    has_hints = bool(response_mime_type) or response_schema is not None
    if not has_hints:
        if require_structured_output:
            raise LLMProviderError(
                f"Provider '{provider_name}' structured output requires "
                "response_mime_type and response_schema"
            )
        return None

    if response_mime_type and response_mime_type != "application/json":
        if require_structured_output:
            raise LLMProviderError(
                f"Provider '{provider_name}' structured output only supports "
                f"application/json, got {response_mime_type}"
            )
        return None

    if response_schema is not None:
        if not isinstance(response_schema, dict):
            raise LLMProviderError(
                f"Provider '{provider_name}' structured output requires "
                "response_schema to be a dict JSON Schema"
            )
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "simpleclaw_structured_response",
                "strict": True,
                "schema": _sanitize_json_schema_for_openai(response_schema),
            },
        }

    if require_structured_output:
        raise LLMProviderError(
            f"Provider '{provider_name}' required structured output "
            "requires response_schema"
        )

    return {"type": "json_object"}


class OpenAIProvider(LLMProvider):
    """OpenAI ChatGPT API 프로바이더."""

    # BIZ-448 — create_router() 가 static provider config 블록에서 골라 전달하는
    # 추가 설정 키. OpenRouter 같은 OpenAI-compatible endpoint 지원용이며,
    # 런타임(user/cron/recipe) 입력으로는 절대 override 되지 않는다.
    EXTRA_CONFIG_KEYS = ("base_url", "extra_body", "default_headers")

    def __init__(
        self,
        model: str,
        api_key: str,
        name: str = "openai",
        base_url: str | None = None,
        extra_body: dict | None = None,
        default_headers: dict | None = None,
    ) -> None:
        """OpenAIProvider를 초기화한다.

        Args:
            model: 사용할 모델 ID (예: gpt-4o, z-ai/glm-5.2).
            api_key: API 키.
            name: 라우터에서 이 백엔드를 식별하는 이름.
            base_url: OpenAI-compatible endpoint URL. None 이면 OpenAI 기본.
            extra_body: 모든 Chat Completions 요청 body 에 주입할 provider별
                확장 필드 (예: OpenRouter ``reasoning.enabled=false``).
            default_headers: 클라이언트 수준 기본 HTTP 헤더.

        Raises:
            LLMAuthError: API 키가 비어있는 경우.
        """
        if not api_key:
            raise LLMAuthError(f"API key missing for provider '{name}' (env var not set)")
        self._model = model
        self._extra_body = dict(extra_body or {})
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
            default_headers=default_headers or None,
        )
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
        system_blocks: list[SystemBlock] | None = None,
        max_tokens: int | None = None,
        response_mime_type: str | None = None,
        response_schema: dict | type | None = None,
        require_structured_output: bool = False,
    ) -> LLMResponse:
        """Chat Completions API로 메시지를 전송하고 응답을 반환한다.

        BIZ-450 — structured output 힌트는 OpenAI/OpenRouter ``response_format``
        으로 매핑한다. required 면 ``json_schema`` 를 사용하고, dict JSON Schema
        가 없으면 API 호출 전에 빠르게 실패한다.
        """
        response_format = _openai_response_format(
            provider_name=self._name,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            require_structured_output=require_structured_output,
        )
        # BIZ-252 — OpenAI 는 prompt caching 마커가 없는 단일 문자열만 받으므로
        # system_blocks 가 있으면 캐시 플래그를 무시하고 텍스트만 이어 붙인다.
        effective_system = flatten_system_blocks(system_blocks, fallback=system_prompt)
        # 시스템 프롬프트를 맨 앞에 배치한 뒤 대화 메시지를 이어붙임
        msg_list: list[dict] = []
        if effective_system:
            msg_list.append({"role": "system", "content": effective_system})
        if messages is not None:
            msg_list.extend(self._convert_messages(messages))
        else:
            msg_list.append({"role": "user", "content": user_message})

        try:
            kwargs: dict = {
                "model": self._model,
                "messages": msg_list,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format
            # BIZ-448 — OpenRouter 등 OpenAI-compatible endpoint 확장 필드 주입
            # (예: GLM reasoning budget 이 답변 토큰을 잠식하지 않도록 비활성화).
            if self._extra_body:
                kwargs["extra_body"] = self._extra_body
            if tools:
                kwargs["tools"] = self._convert_tools(tools)
            # BIZ-297 — max_tokens 가 지정되면 모델 종류에 맞는 필드명으로 cap 을
            # 박는다. None 이면 기존 동작(필드 미지정 → API 기본값) 유지.
            if max_tokens:
                kwargs[_max_tokens_field(self._model)] = max_tokens

            response = await self._client.chat.completions.create(**kwargs)
        except openai.AuthenticationError as e:
            raise LLMAuthError(f"OpenAI auth failed: {e}") from e
        except openai.APIError as e:
            raise LLMProviderError(f"OpenAI API error: {e}") from e

        choice = response.choices[0] if response.choices else None
        text = choice.message.content or "" if choice else ""
        # BIZ-452 — 출력 cap 잘림(finish_reason=length) 을 raw 응답 없이도
        # 진단할 수 있게 종료 사유를 LLMResponse 에 보존한다.
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        if not isinstance(finish_reason, str):
            finish_reason = None
        if response_format is not None and finish_reason == "length":
            # structured output 이 토큰 cap 에서 잘리면 JSON 이 깨진다 — 호출자
            # repair/fallback 경로 진단용 경고 (프롬프트/응답 본문은 남기지 않음).
            logger.warning(
                "OpenAI structured output hit output-token cap "
                "(finish_reason=length backend=%s model=%s)",
                self._name,
                self._model,
            )

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
            finish_reason=finish_reason,
            diagnostics={"finish_reason": finish_reason} if finish_reason else None,
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
        """Chat Completions streaming — text 델타를 ``on_text_delta`` 로 흘린다.

        BIZ-290: ``stream=True`` + ``stream_options={"include_usage": True}`` 으로
        SSE 청크를 받아 ``delta.content`` 마다 콜백을 await 한다. ``delta.tool_calls``
        는 ``index`` 별 슬롯에 ``id``/``name``/``arguments`` 를 누적했다가 마지막에
        JSON 파싱하여 최종 LLMResponse 의 tool_calls 로 반환한다. ``on_text_delta=None``
        이면 send() 와 동일한 모양의 LLMResponse 를 돌려준다 — 통합 인터페이스.

        스트림 중 콜백 예외는 흡수해 누적은 완수한다 (Claude/Gemini 와 동일 정책 —
        sink 측 일시 오류가 LLM 응답 자체를 깨뜨리지 않도록).

        BIZ-450 — structured output 힌트는 send() 와 동일하게 ``response_format``
        으로 매핑해 provider contract 를 일관되게 유지한다.
        """
        response_format = _openai_response_format(
            provider_name=self._name,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            require_structured_output=require_structured_output,
        )
        effective_system = flatten_system_blocks(system_blocks, fallback=system_prompt)
        msg_list: list[dict] = []
        if effective_system:
            msg_list.append({"role": "system", "content": effective_system})
        if messages is not None:
            msg_list.extend(self._convert_messages(messages))
        else:
            msg_list.append({"role": "user", "content": user_message})

        kwargs: dict = {
            "model": self._model,
            "messages": msg_list,
            "stream": True,
            # 종료 청크에 usage 메타데이터를 포함시킨다. 미지정 시 stream 응답에
            # usage 가 빠져 input/output_tokens 추적이 불가능.
            "stream_options": {"include_usage": True},
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        # BIZ-448 — send() 와 동일하게 endpoint 확장 필드를 스트리밍에도 주입.
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        # BIZ-297 — send() 와 동일하게 모델 종류에 맞는 cap 필드 매핑.
        if max_tokens:
            kwargs[_max_tokens_field(self._model)] = max_tokens

        text_parts: list[str] = []
        # index 별 누적 슬롯. OpenAI 는 tool_call 의 id/name/arguments 를 첫 청크
        # 위주로 보내고, arguments JSON 문자열은 여러 청크에 걸쳐 분할 전송한다.
        tc_accumulator: dict[int, dict] = {}
        usage = None
        finish_reason: str | None = None

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                # 종료 청크: choices=[] 이고 usage 만 채워진다 (include_usage 옵션).
                if chunk.usage is not None:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    }
                if not chunk.choices:
                    continue
                # BIZ-452 — 종료 청크의 finish_reason 을 보존해 send() 와 동일한
                # truncation 진단이 스트리밍에서도 가능하게 한다.
                chunk_finish = getattr(chunk.choices[0], "finish_reason", None)
                if isinstance(chunk_finish, str) and chunk_finish:
                    finish_reason = chunk_finish
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                content_delta = getattr(delta, "content", None) or ""
                if content_delta:
                    text_parts.append(content_delta)
                    if on_text_delta is not None:
                        try:
                            await on_text_delta(content_delta)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "OpenAI stream on_text_delta callback raised: %s",
                                exc,
                            )

                tcs_delta = getattr(delta, "tool_calls", None) or []
                for tc_delta in tcs_delta:
                    idx = tc_delta.index
                    slot = tc_accumulator.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc_delta.id:
                        slot["id"] = tc_delta.id
                    fn = getattr(tc_delta, "function", None)
                    if fn is not None:
                        fn_name = getattr(fn, "name", None)
                        if fn_name:
                            slot["name"] = fn_name
                        fn_args = getattr(fn, "arguments", None)
                        if fn_args:
                            slot["arguments"] += fn_args
        except openai.AuthenticationError as e:
            raise LLMAuthError(f"OpenAI auth failed: {e}") from e
        except openai.APIError as e:
            raise LLMProviderError(f"OpenAI API error: {e}") from e

        text = "".join(text_parts)
        if response_format is not None and finish_reason == "length":
            logger.warning(
                "OpenAI structured output hit output-token cap "
                "(finish_reason=length backend=%s model=%s)",
                self._name,
                self._model,
            )

        tool_calls: list[ToolCall] | None = None
        if tc_accumulator:
            tc_list: list[ToolCall] = []
            for idx in sorted(tc_accumulator.keys()):
                slot = tc_accumulator[idx]
                try:
                    args = json.loads(slot["arguments"]) if slot["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                tc_list.append(
                    ToolCall(
                        id=slot["id"],
                        name=slot["name"],
                        arguments=args,
                    )
                )
            if tc_list:
                tool_calls = tc_list

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            diagnostics={"finish_reason": finish_reason} if finish_reason else None,
        )
