"""도구 에러/출력 문자열 sanitizer — ReAct Observation 재주입 방어선.

PRD §3.5.6 "Tool 에러 sanitization" 의 구현 모듈. 도구 실행이 던진
stderr / Exception 메시지가 LLM 의 다음 턴 input(``role=tool`` 메시지)
으로 흘러들어가기 직전에:

1. XML role tag (``<tool_call>``, ``<assistant>``, ``<|im_start|>`` 등)
   - 모델이 role-confusion framing 으로 해석할 수 있는 구조적 토큰을 제거
2. CDATA 섹션 / 마크다운 코드 펜스
3. ANSI 이스케이프, 제어 문자 (\\x00-\\x08, \\x0b-\\x1f, \\x7f 등 제외:
   \\t \\n \\r 은 유지) 정규화
4. ``Ignore previous instructions`` 류 instruction-hijack 패턴 감지 시
   ``[SUSPICIOUS_INPUT]`` 경고 prefix 부착 — 원문은 보존
5. ``TOOL_ERROR_MAX_LEN`` 으로 길이 캡 (기본 2000자)
6. ``[TOOL_ERROR]`` 봉투 prefix 부착

설계 결정 (Hermes Agent PR #26823 에서 가져온 부분):
- wire-layer (JSON 직렬화) 는 이미 안전하므로 이 모듈은 *모델이 읽는*
  토큰만 본다. defense-in-depth 한 줄짜리 helper.
- 두 가지 변형 제공:
  * ``sanitize_tool_error`` — 실패 경로. envelope prefix + instruction 감지
  * ``sanitize_tool_output`` — 성공 경로. envelope 없음, instruction 감지
    없음 (legitimate grep 결과의 false positive 방지). 구조 토큰만 제거.

스킬/subprocess stderr 가 SQLite 히스토리에 저장될 때도 sanitize 된
사본이 들어가야 다음 턴 컨텍스트가 깨끗하다 — orchestrator 가 매 도구
실행 후 결과를 messages 에 ``role=tool`` 로 append 하는 지점에서 호출.
"""

from __future__ import annotations

import re

__all__ = [
    "TOOL_ERROR_MAX_LEN",
    "TOOL_ERROR_PREFIX",
    "sanitize_tool_error",
    "sanitize_tool_output",
]


TOOL_ERROR_PREFIX = "[TOOL_ERROR] "
TOOL_ERROR_MAX_LEN = 2000

# XML 형태의 역할 태그. 모델 채팅 템플릿에서 의미를 가지는 토큰만 좁게
# 지정한다 — 임의 XML(`<ParseError>` 같은 진단 메시지) 은 건드리지 않는다.
_ROLE_TAG_RE = re.compile(
    r"</?(?:tool_call|function_call|result|response|output|input"
    r"|system|assistant|user)>",
    re.IGNORECASE,
)

# ChatML / GPT 계열의 ``<|im_start|>system`` 류 separator. role-confusion
# 의 1순위 attack surface 라 별도 패턴.
_CHATML_TOKEN_RE = re.compile(
    r"<\|(?:im_start|im_end|im_sep|system|assistant|user|function|tool)\|>",
    re.IGNORECASE,
)

_CDATA_RE = re.compile(r"<!\[CDATA\[.*?\]\]>", re.DOTALL)
_FENCE_OPEN_RE = re.compile(
    r"^\s*```(?:json|xml|html|markdown|sh|bash|python)?\s*",
    re.MULTILINE,
)
_FENCE_CLOSE_RE = re.compile(r"\s*```\s*$", re.MULTILINE)

# ANSI CSI / OSC / 단순 색상 sequence — guard.py 의 _normalize 와 동일하게
# 처리해 두 모듈이 같은 규칙으로 정규화된 입력을 본다.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*\x07")

# C0 제어 문자(0x00-0x1f) 중 의미 있는 공백(\t \n \r) 만 남기고 모두 제거.
# DEL(0x7f) 도 제거. 그 외 유니코드는 건드리지 않는다(unicodedata.normalize
# 는 guard.py 측에서 NFKC 로 이미 처리).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Instruction-hijack 패턴 — 단순 stderr 에 섞여 들어왔을 때 모델이
# 명령으로 오해하지 않게 prefix 로 표시한다. 너무 공격적인 필터는
# legitimate 한 문서·grep 출력의 false positive 를 만드니 sanitize_tool_error
# (실패 경로) 에서만 적용.
_INSTRUCTION_HIJACK_RE = re.compile(
    r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b"
    r"(?:previous|prior|above|earlier|all|everything)\b"
    r"(?:[^.\n]{0,40}\b"
    r"(?:instructions?|prompts?|rules?|messages?|context|conversation)\b)?",
    re.IGNORECASE,
)


def _strip_framing(text: str) -> str:
    """공통 framing 토큰 제거 — error/output 경로가 공유한다."""
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _CDATA_RE.sub("", text)
    text = _ROLE_TAG_RE.sub("", text)
    text = _CHATML_TOKEN_RE.sub("", text)
    text = _FENCE_OPEN_RE.sub("", text)
    text = _FENCE_CLOSE_RE.sub("", text)
    return text


def sanitize_tool_error(message: str | None) -> str:
    """도구 실행 실패 시 LLM 에 노출되는 에러 문자열을 정화한다.

    - framing 토큰을 제거하여 role-confusion 차단
    - instruction-hijack 패턴이 보이면 ``[SUSPICIOUS_INPUT]`` 경고 prefix
    - ``TOOL_ERROR_MAX_LEN`` 으로 길이 캡
    - 결과 앞에 ``[TOOL_ERROR] `` 봉투 부착
    """
    if not message:
        return TOOL_ERROR_PREFIX.rstrip() + " "

    sanitized = _strip_framing(message)

    if _INSTRUCTION_HIJACK_RE.search(sanitized):
        sanitized = "[SUSPICIOUS_INPUT] " + sanitized

    if len(sanitized) > TOOL_ERROR_MAX_LEN:
        sanitized = sanitized[: TOOL_ERROR_MAX_LEN - 3] + "..."

    return TOOL_ERROR_PREFIX + sanitized


def sanitize_tool_output(message: str | None) -> str:
    """성공 경로의 도구 출력을 정화한다.

    실패 경로와 달리 ``[TOOL_ERROR]`` 봉투를 붙이지 않고, instruction-hijack
    flag 도 부착하지 않는다 (legitimate grep / 문서 페이지에서의
    false positive 방지). framing 토큰 / 제어 문자만 제거.

    길이 캡은 호출측의 ``[:3000]`` 슬라이스를 그대로 두고, 여기서는
    하지 않는다.
    """
    if not message:
        return ""
    return _strip_framing(message)
