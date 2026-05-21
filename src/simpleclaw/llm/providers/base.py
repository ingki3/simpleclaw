"""LLM 프로바이더의 추상 기본 클래스.

모든 LLM 프로바이더(Claude, OpenAI, Gemini, CLI)는 이 클래스를 상속하여
send() 메서드를 구현해야 한다. 라우터는 이 인터페이스만 바라보므로
새 프로바이더 추가 시 기존 코드 변경 없이 확장할 수 있다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from simpleclaw.llm.models import LLMResponse, SystemBlock, ToolDefinition


class LLMProvider(ABC):
    """모든 LLM 프로바이더(API 및 CLI)의 기본 클래스."""

    @abstractmethod
    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
        tools: list[ToolDefinition] | None = None,
        system_blocks: list[SystemBlock] | None = None,
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

        Returns:
            LLMResponse: 텍스트 응답(또는 tool_calls)과 메타데이터를 담은 객체.
        """


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
