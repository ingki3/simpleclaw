"""Anthropic Claude API 프로바이더 — Native Function Calling (Tool Use) 지원.

Anthropic의 Messages API를 사용하여 Claude 모델과 통신한다.
시스템 프롬프트는 별도의 system 파라미터로 전달되며,
멀티턴 대화는 messages 리스트를 그대로 API에 전달한다.

Tool Use:
  tools가 주어지면 ToolDefinition을 Claude의 ToolParam으로 변환하여
  API에 전달하고, 응답에서 ToolUseBlock을 감지하여 ToolCall로 반환한다.
  도구 결과 메시지(role=tool)는 ToolResultBlockParam으로 변환한다.

Prompt Caching (BIZ-252):
  ``system_blocks`` 가 주어지면 ``cache=True`` 인 블록 끝에
  ``cache_control={"type": "ephemeral", "ttl": "1h"}`` 마커를 부착하여
  세션 간 1시간 prompt cache 를 활성화한다. 1h TTL 은
  ``anthropic-beta: extended-cache-ttl-2025-04-11`` 헤더가 필요하다.
  ``cache_creation_input_tokens`` / ``cache_read_input_tokens`` 를 응답
  usage 에 노출하고 INFO 로깅하여 운영자가 hit rate 를 추적할 수 있게 한다.
"""

from __future__ import annotations

import logging

import anthropic

from simpleclaw.llm.models import (
    LLMAuthError,
    LLMProviderError,
    LLMResponse,
    SystemBlock,
    ToolCall,
    ToolDefinition,
)
from simpleclaw.llm.providers.base import LLMProvider, TextDeltaCallback

logger = logging.getLogger(__name__)

# 1시간 cache TTL 활성화 베타 헤더. 기본 5분 TTL 은 헤더 없이도 동작하지만,
# SimpleClaw 의 세션 간 호출 패턴에는 1h 가 훨씬 유리하다.
_EXTENDED_CACHE_TTL_BETA = "extended-cache-ttl-2025-04-11"
_CACHE_TTL = "1h"


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
    def _build_system_param(
        system_blocks: list[SystemBlock] | None,
        system_prompt: str,
    ) -> list[dict] | str | None:
        """Anthropic ``system`` 파라미터를 만든다.

        ``system_blocks`` 가 있으면 각 블록을 ``{"type": "text", "text": ...}``
        content block 으로 변환하고, ``cache=True`` 인 블록 끝에
        ``cache_control`` 마커를 부착한다. 빈 텍스트 블록은 건너뛴다.
        ``system_blocks`` 가 없으면 기존 동작 그대로 단일 문자열을 반환한다.
        """
        if system_blocks:
            content: list[dict] = []
            for block in system_blocks:
                if not block.text:
                    continue
                entry: dict = {"type": "text", "text": block.text}
                if block.cache:
                    entry["cache_control"] = {"type": "ephemeral", "ttl": _CACHE_TTL}
                content.append(entry)
            return content or None
        return system_prompt or None

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
        """Claude Messages API로 메시지를 전송하고 응답을 반환한다.

        BIZ-427 — structured output 은 아직 미구현. required 면 명확히 거부하고,
        아니면 힌트를 무시한다 (기존 호출 회귀 0).
        """
        self._reject_required_structured_output(
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            require_structured_output=require_structured_output,
        )
        if messages is not None:
            msg_list = self._convert_messages(messages)
        else:
            msg_list = [{"role": "user", "content": user_message}]

        system_param = self._build_system_param(system_blocks, system_prompt)
        has_cached_block = bool(
            system_blocks and any(b.cache and b.text for b in system_blocks)
        )

        try:
            kwargs: dict = {
                "model": self._model,
                "max_tokens": max_tokens if max_tokens else 4096,
                "messages": msg_list,
            }
            if system_param is not None:
                kwargs["system"] = system_param
            if tools:
                kwargs["tools"] = self._convert_tools(tools)
            # 1h 캐시 TTL 은 베타 헤더로만 활성화된다. 캐시 마커가 없으면 헤더도 생략해
            # 베타 surface 노출을 최소화한다.
            if has_cached_block:
                kwargs["extra_headers"] = {"anthropic-beta": _EXTENDED_CACHE_TTL_BETA}

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

        usage: dict = {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
        }
        # BIZ-252 — prompt caching 메트릭. Anthropic SDK 가 두 필드를 반환할 때만 노출한다.
        cache_creation = getattr(message.usage, "cache_creation_input_tokens", None)
        cache_read = getattr(message.usage, "cache_read_input_tokens", None)
        if cache_creation is not None:
            usage["cache_creation_input_tokens"] = cache_creation
        if cache_read is not None:
            usage["cache_read_input_tokens"] = cache_read
        if has_cached_block and (cache_creation is not None or cache_read is not None):
            logger.info(
                "Claude prompt cache: read=%s created=%s base_input=%s",
                cache_read or 0, cache_creation or 0, message.usage.input_tokens,
            )

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls,
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
        """Claude Messages API streaming — text 델타를 ``on_text_delta`` 로 흘린다.

        BIZ-259: ``client.messages.stream`` 컨텍스트 매니저로 SSE 이벤트를 받아
        ``text_delta`` 이벤트마다 콜백을 await 한다. ``tool_use`` 블록은 누적해
        최종 LLMResponse 의 tool_calls 로 반환한다. 콜백이 None 이면 그냥 누적만
        해서 send() 와 동일한 동작이 된다 — 호출 측 통일 인터페이스.

        스트림 중 콜백 예외는 흡수해 누적은 완수한다 (sink rate-limit guard 가
        간헐적 텔레그램 API 오류로 죽지 않도록).

        BIZ-427 — structured output 은 아직 미구현. required 면 명확히 거부.
        """
        self._reject_required_structured_output(
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            require_structured_output=require_structured_output,
        )
        if messages is not None:
            msg_list = self._convert_messages(messages)
        else:
            msg_list = [{"role": "user", "content": user_message}]

        system_param = self._build_system_param(system_blocks, system_prompt)
        has_cached_block = bool(
            system_blocks and any(b.cache and b.text for b in system_blocks)
        )

        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens if max_tokens else 4096,
            "messages": msg_list,
        }
        if system_param is not None:
            kwargs["system"] = system_param
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
        if has_cached_block:
            kwargs["extra_headers"] = {"anthropic-beta": _EXTENDED_CACHE_TTL_BETA}

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        final_message = None
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    # text 델타: ``content_block_delta`` 의 ``text_delta`` 변형.
                    delta_text = ""
                    if getattr(event, "type", "") == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None and getattr(delta, "type", "") == "text_delta":
                            delta_text = getattr(delta, "text", "") or ""
                    if delta_text:
                        text_parts.append(delta_text)
                        if on_text_delta is not None:
                            try:
                                await on_text_delta(delta_text)
                            except Exception as exc:  # noqa: BLE001
                                # sink 측 일시 오류(텔레그램 rate limit 등) 가 LLM
                                # 응답 자체를 깨뜨리지 않도록 흡수. 운영자 점검용으로 한 줄만.
                                logger.warning(
                                    "Claude stream on_text_delta callback raised: %s",
                                    exc,
                                )
                final_message = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMAuthError(f"Claude auth failed: {e}") from e
        except anthropic.APIError as e:
            raise LLMProviderError(f"Claude API error: {e}") from e

        # 최종 메시지에서 tool_use 블록을 추출. text 는 누적된 델타로부터.
        text = "".join(text_parts)
        usage: dict = {
            "input_tokens": 0,
            "output_tokens": 0,
        }
        if final_message is not None:
            if final_message.content:
                for block in final_message.content:
                    if block.type == "tool_use":
                        tool_calls.append(
                            ToolCall(
                                id=block.id,
                                name=block.name,
                                arguments=block.input
                                if isinstance(block.input, dict)
                                else {},
                            )
                        )
            if final_message.usage is not None:
                usage["input_tokens"] = final_message.usage.input_tokens
                usage["output_tokens"] = final_message.usage.output_tokens
                cache_creation = getattr(
                    final_message.usage, "cache_creation_input_tokens", None
                )
                cache_read = getattr(
                    final_message.usage, "cache_read_input_tokens", None
                )
                if cache_creation is not None:
                    usage["cache_creation_input_tokens"] = cache_creation
                if cache_read is not None:
                    usage["cache_read_input_tokens"] = cache_read
                if has_cached_block and (
                    cache_creation is not None or cache_read is not None
                ):
                    logger.info(
                        "Claude prompt cache (stream): read=%s created=%s base_input=%s",
                        cache_read or 0,
                        cache_creation or 0,
                        final_message.usage.input_tokens,
                    )

        return LLMResponse(
            text=text,
            backend_name=self._name,
            model=self._model,
            usage=usage,
            tool_calls=tool_calls or None,
        )

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
