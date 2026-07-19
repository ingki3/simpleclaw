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
    transport: str | None = None
    profile: str | None = None
    api_key_env: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    timeout: int = 120


@dataclass(frozen=True)
class LLMRoute:
    """A named role route resolved to a primary backend and optional retry."""

    name: str
    primary: str
    retry: str | None = None


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


@dataclass(frozen=True)
class MultimodalAttachment:
    """Provider-neutral 멀티모달 첨부 1건.

    Telegram 등 채널 레이어는 외부 파일 ID를 LLM provider에 직접 노출하지 않고,
    인증 후 다운로드한 bytes와 MIME type만 이 구조체에 담는다. 이미지뿐 아니라
    PDF/텍스트 같은 문서 첨부도 같은 현재 turn 컨텍스트로 전달된다. ``path``는
    채널이 안전한 sandbox에 저장한 사본 위치이며, 영속 대화 DB에는 저장하지 않는
    운영/디버깅 메타데이터다. Gemini provider는 지원 MIME에 한해 이 값을
    ``types.Part.from_bytes(data=..., mime_type=...)`` 로 변환한다.
    """
    data: bytes
    mime_type: str
    name: str | None = None
    path: str | None = None
    size_bytes: int | None = None


@dataclass
class SystemBlock:
    """시스템 프롬프트의 한 세그먼트.

    BIZ-252 — Anthropic prompt caching 적용을 위해 시스템 프롬프트를 의미 단위(페르소나,
    스킬, RAG 등) 로 쪼개기 위한 컨테이너. ``cache=True`` 인 블록 끝에 캐시 경계 마커가
    부착되어 해당 지점까지의 누적 prefix 가 캐시 키가 된다.
    Anthropic 외 프로바이더는 ``cache`` 플래그를 무시하고 ``text`` 만 사용한다.
    """
    text: str
    cache: bool = False


@dataclass
class LLMRequest:
    """LLM에 보낼 요청 데이터.

    backend_name이 None이면 라우터의 기본 백엔드가 사용된다.
    messages가 주어지면 멀티턴 대화로 전송하고, 없으면 user_message 단일 턴으로 전송한다.
    tools가 주어지면 Native Function Calling 모드로 동작한다.
    system_blocks 가 주어지면 system_prompt 대신 사용되며, Anthropic 프로바이더는
    각 블록의 ``cache`` 플래그를 캐시 경계로 해석한다. 그 외 프로바이더는 단일 문자열로 합친다.

    BIZ-297 — ``max_tokens`` 가 None 이 아니면 프로바이더가 해당 값을 출력 토큰 cap
    으로 사용한다. None 이면 각 프로바이더의 기존 기본값(Claude 4096, OpenAI/Gemini
    는 API 모델 기본값) 으로 fallback — 호환성 1순위.

    BIZ-427 — ``response_mime_type``/``response_schema`` 는 provider-neutral
    structured output 힌트다. schema-constrained 출력을 지원하는 프로바이더
    (현재 Gemini)는 네이티브 config 로 매핑하고, 미지원 프로바이더는
    ``require_structured_output=False`` 면 조용히 무시, ``True`` 면 명확한
    ``LLMProviderError`` 를 던진다 — 호출자가 fallback 여부를 결정하게 한다.

    BIZ-453 — ``reasoning`` 은 provider-neutral reasoning hint 다
    (``{"enabled": bool, "effort": str, "budget_tokens": int}``). 지원
    프로바이더(현재 Gemini)는 native thinking config 로 매핑하고, 미지원
    프로바이더는 조용히 무시한다. 이 값은 config 로더에서만 채워지며
    사용자/runtime tool 입력으로 설정되어서는 안 된다.
    """
    system_prompt: str = ""
    user_message: str = ""
    route_name: str | None = None
    backend_name: str | None = None
    messages: list[dict] | None = None
    tools: list[ToolDefinition] | None = None
    system_blocks: list[SystemBlock] | None = None
    max_tokens: int | None = None
    response_mime_type: str | None = None
    response_schema: dict | type | None = None
    require_structured_output: bool = False
    reasoning: dict | None = None
    required_capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass
class LLMResponse:
    """LLM으로부터 받은 응답 데이터.

    tool_calls가 있으면 LLM이 도구 호출을 요청한 것이고,
    없으면 text가 최종 응답이다.
    usage는 프로바이더가 토큰 사용량을 지원할 경우 input_tokens/output_tokens 딕셔너리로 채워진다.
    finish_reason/diagnostics는 provider별 종료 사유와 디버깅 메타데이터를 선택적으로
    보존한다. 기존 호출자는 무시해도 되는 부가 정보로, 빈 응답/차단/토큰 0 같은
    운영 진단을 사용자가 보낸 원본 payload 없이도 로그에 남기기 위한 필드다.
    """
    text: str = ""
    backend_name: str = ""
    model: str = ""
    usage: dict | None = None
    tool_calls: list[ToolCall] | None = None
    # provider-specific 원본 데이터 (예: Gemini의 Content 객체).
    # 다음 호출 시 thought_signature 등을 보존하기 위해 사용한다.
    raw_assistant_message: object | None = None
    finish_reason: str | None = None
    diagnostics: dict | None = None


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
