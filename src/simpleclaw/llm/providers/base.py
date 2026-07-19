"""LLM 프로바이더의 추상 기본 클래스.

모든 LLM 프로바이더(Claude, OpenAI, Gemini, CLI)는 이 클래스를 상속하여
send() 메서드를 구현해야 한다. 라우터는 이 인터페이스만 바라보므로
새 프로바이더 추가 시 기존 코드 변경 없이 확장할 수 있다.

스트리밍(BIZ-259):
  ``stream()`` 메서드는 send() 와 동일한 입력을 받지만 텍스트 델타가 생성될
  때마다 ``on_text_delta`` 콜백을 await 호출한다. 호출이 끝나면 send() 와
  같은 LLMResponse 를 반환한다. 기본 구현은 send() 결과를 한 번에 콜백으로
  흘려보내는 fallback — 실제 스트리밍을 지원하는 프로바이더만 오버라이드한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable

from simpleclaw.llm.models import (
    LLMProviderError,
    LLMResponse,
    SystemBlock,
    ToolDefinition,
)

TextDeltaCallback = Callable[[str], Awaitable[None]]


class LLMProvider(ABC):
    """모든 LLM 프로바이더(API 및 CLI)의 기본 클래스."""

    def _reject_required_structured_output(
        self,
        *,
        response_mime_type: str | None,
        response_schema: dict | type | None,
        require_structured_output: bool,
        reason: str = "does not support structured output yet",
    ) -> None:
        """schema-constrained 출력을 보장할 수 없을 때의 공통 가드 (BIZ-427/430).

        ``require_structured_output=False`` 면 힌트를 조용히 무시해 기존 호출
        회귀를 막고, ``True`` 면 명확한 ``LLMProviderError`` 를 던져 호출자가
        fallback 여부를 결정하게 한다 — 잘못된 JSON 을 조용히 돌려주는 것보다
        빠른 실패가 안전하다.

        BIZ-430 — "required" 계약은 mime/schema 힌트의 존재 여부가 아니라
        호출자의 보장 요구(``require_structured_output=True``)만으로 결정된다.
        힌트 없는 required 요청도 미지원 provider 에서는 즉시 거부한다.
        """
        # 힌트 인자는 시그니처 통일용으로만 받는다 — 거부 판단에는 쓰지 않는다.
        del response_mime_type, response_schema
        if require_structured_output:
            name = getattr(self, "_name", type(self).__name__)
            raise LLMProviderError(f"Provider '{name}' {reason}")

    @abstractmethod
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
        reasoning: dict | None = None,
    ) -> LLMResponse:
        """LLM에 메시지를 전송하고 응답을 반환한다.

        Args:
            system_prompt: 시스템 프롬프트. ``system_blocks`` 가 주어지면 무시된다.
            user_message: 단일 턴 사용자 메시지.
            messages: 멀티턴 대화 이력. 주어지면 user_message 대신 사용된다.
            tools: 도구 정의 목록. 주어지면 Native Function Calling 모드로 동작한다.
            system_blocks: 시스템 프롬프트의 세그먼트 목록. Anthropic 프로바이더는
                ``cache=True`` 블록 끝에 prompt caching 경계 마커를 부착한다.
                그 외 프로바이더는 모든 블록을 단일 문자열로 합친다.
            max_tokens: 출력 토큰 cap. None 이면 프로바이더 기본값을 사용한다 (BIZ-297).
            response_mime_type: structured output MIME 힌트 (BIZ-427, 예:
                ``application/json``). 미지원 프로바이더는 무시할 수 있다.
            response_schema: structured output JSON Schema (BIZ-427). 지원
                프로바이더는 네이티브 config 로 매핑한다.
            require_structured_output: True 면 schema-constrained 출력을 보장할
                수 없는 프로바이더는 조용히 무시하는 대신 ``LLMProviderError``
                를 던져야 한다 (BIZ-427).
            reasoning: provider-neutral reasoning hint (BIZ-453,
                ``{"enabled": bool, "effort": str, "budget_tokens": int}``).
                지원 프로바이더(Gemini)는 native thinking config 로 매핑하고,
                미지원 프로바이더는 조용히 무시해야 한다 — structured output
                과 달리 보장 계약이 아니라 품질 힌트이기 때문.

        Returns:
            LLMResponse: 텍스트 응답(또는 tool_calls)과 메타데이터를 담은 객체.
        """

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
        reasoning: dict | None = None,
    ) -> LLMResponse:
        """LLM 응답을 스트리밍하면서 ``on_text_delta`` 로 텍스트 델타를 흘려보낸다.

        기본 구현(fallback): 실제 스트리밍을 지원하지 않는 프로바이더용. send() 를
        호출해 완성된 응답을 한 번에 콜백으로 흘려보낸 뒤 동일 LLMResponse 를
        그대로 돌려준다. 호출 측에서 보면 "스트리밍 호출했지만 한 덩이가 한 번에
        도착" 한 것과 동일하므로 sink 측에서 자연스럽게 placeholder 갱신 후 종료.

        structured output 필드(BIZ-427)는 send() 로 그대로 위임한다 — 수용/거부
        판단은 각 프로바이더의 send() 정책을 따른다.
        """
        response = await self.send(
            system_prompt=system_prompt,
            user_message=user_message,
            messages=messages,
            tools=tools,
            system_blocks=system_blocks,
            max_tokens=max_tokens,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            require_structured_output=require_structured_output,
            reasoning=reasoning,
        )
        if on_text_delta is not None and response.text:
            await on_text_delta(response.text)
        return response


def flatten_system_blocks(
    blocks: list[SystemBlock] | None,
    fallback: str = "",
    separator: str = "\n\n---\n\n",
) -> str:
    """``system_blocks`` 를 단일 문자열로 합쳐 캐시 미지원 프로바이더에 전달한다.

    빈 텍스트 블록은 제외하여 ``separator`` 가 연속으로 찍히는 것을 막는다.
    ``blocks`` 가 None 이면 ``fallback`` 을 그대로 반환한다.
    """
    if not blocks:
        return fallback
    parts = [b.text for b in blocks if b.text]
    return separator.join(parts) if parts else fallback
