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
        system_blocks: list[SystemBlock] | None = None,
    ) -> LLMResponse:
        """Chat Completions API로 메시지를 전송하고 응답을 반환한다."""
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

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools: list[ToolDefinition] | None = None,
        system_blocks: list[SystemBlock] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> LLMResponse:
        """Chat Completions streaming — text 델타를 ``on_text_delta`` 로 흘린다.

        BIZ-290: ``stream=True`` + ``stream_options={"include_usage": True}`` 으로
        SSE 청크를 받아 ``delta.content`` 마다 콜백을 await 한다. ``delta.tool_calls``
        는 ``index`` 별 슬롯에 ``id``/``name``/``arguments`` 를 누적했다가 마지막에
        JSON 파싱하여 최종 LLMResponse 의 tool_calls 로 반환한다. ``on_text_delta=None``
        이면 send() 와 동일한 모양의 LLMResponse 를 돌려준다 — 통합 인터페이스.

        스트림 중 콜백 예외는 흡수해 누적은 완수한다 (Claude/Gemini 와 동일 정책 —
        sink 측 일시 오류가 LLM 응답 자체를 깨뜨리지 않도록).
        """
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
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        text_parts: list[str] = []
        # index 별 누적 슬롯. OpenAI 는 tool_call 의 id/name/arguments 를 첫 청크
        # 위주로 보내고, arguments JSON 문자열은 여러 청크에 걸쳐 분할 전송한다.
        tc_accumulator: dict[int, dict] = {}
        usage = None

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
        )
