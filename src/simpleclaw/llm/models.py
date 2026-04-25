"""LLM 라우팅 계층의 데이터 모델 정의.

LLM 백엔드 설정, 요청/응답 구조체, 에러 계층을 정의한다.
모든 프로바이더(Claude, OpenAI, Gemini, CLI)가 공통으로 사용하는
데이터 타입을 한 곳에 모아 순환 임포트를 방지한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BackendType(Enum):
    """LLM 백엔드 유형 — API 호출 또는 CLI 서브프로세스."""
    API = "api"
    CLI = "cli"


@dataclass
class LLMBackend:
    """단일 LLM 백엔드의 설정 정보.

    config.yaml의 providers 섹션 한 항목이 이 객체 하나에 매핑된다.
    API 백엔드는 model/api_key_env를, CLI 백엔드는 command/args를 사용한다.
    """
    name: str
    backend_type: BackendType
    model: str
    api_key_env: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    timeout: int = 120


@dataclass
class ToolDefinition:
    """Provider-agnostic 도구 정의 (JSON Schema 기반).

    각 프로바이더의 send()가 이 정의를 SDK 네이티브 형식으로 변환한다.
    parameters는 JSON Schema object 형식으로 작성한다.
    """
    name: str
    description: str
    parameters: dict = field(default_factory=dict)


@dataclass
class ToolCall:
    """LLM이 반환한 도구 호출 1건.

    Attributes:
        id: 프로바이더가 할당한 호출 ID (Claude/OpenAI의 결과 매칭에 필요)
        name: 호출할 도구 이름
        arguments: 파싱된 인자 딕셔너리
    """
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class LLMRequest:
    """LLM에 보낼 요청 데이터.

    backend_name이 None이면 라우터의 기본 백엔드가 사용된다.
    messages가 주어지면 멀티턴 대화로 전송하고, 없으면 user_message 단일 턴으로 전송한다.
    tools가 주어지면 Native Function Calling 모드로 동작한다.
    """
    system_prompt: str = ""
    user_message: str = ""
    backend_name: str | None = None
    messages: list[dict] | None = None
    tools: list[ToolDefinition] | None = None


@dataclass
class LLMResponse:
    """LLM으로부터 받은 응답 데이터.

    tool_calls가 있으면 LLM이 도구 호출을 요청한 것이고,
    없으면 text가 최종 응답이다.
    usage는 프로바이더가 토큰 사용량을 지원할 경우 input_tokens/output_tokens 딕셔너리로 채워진다.
    """
    text: str = ""
    backend_name: str = ""
    model: str = ""
    usage: dict | None = None
    tool_calls: list[ToolCall] | None = None
    # provider-specific 원본 데이터 (예: Gemini의 Content 객체).
    # 다음 호출 시 thought_signature 등을 보존하기 위해 사용한다.
    raw_assistant_message: object | None = None


# ---------------------------------------------------------------------------
# 에러 계층: LLMError를 최상위로 하여 세분화된 예외를 정의한다.
# 호출자는 LLMError로 일괄 캐치하거나, 하위 타입별로 분기 처리할 수 있다.
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """LLM 관련 에러의 기본 클래스."""


class LLMConfigError(LLMError):
    """설정 에러 — 설정 누락, 알 수 없는 백엔드 이름 등."""


class LLMAuthError(LLMError):
    """인증 에러 — API 키 누락 또는 유효하지 않은 키."""


class LLMProviderError(LLMError):
    """프로바이더 에러 — API 호출 실패, 네트워크 오류 등."""


class LLMTimeoutError(LLMError):
    """CLI 프로세스 타임아웃."""


class LLMCLINotFoundError(LLMError):
    """시스템에서 CLI 도구를 찾을 수 없음."""
