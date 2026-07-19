"""LLM 기반 turn 분석(TurnAnalysis) 모듈.

BIZ-426 — 운영자는 follow-up/맥락/복잡도 판단을 키워드로 하는 방식을 원하지
않는다. 이 모듈은 일반 사용자 turn 앞단에서 LLM 한 번의 structured JSON
호출로 다음을 함께 판단한다:

- follow-up 여부와 맥락 복원된 내부 실행용 질문(``normalized_question``)
- 복원이 애매할 때의 clarify 필요성과 선택지(``ambiguity_options``)
- capability routing 에 쓸 도메인/의도(``domains``/``intents``)
- 실행 경로(``route``)와 복잡도 슬롯 플래그(현재 사실/규칙/계산 등)

설계 결정:
- ``original_text`` 는 DB 저장/감사용 원문으로 절대 덮어쓰지 않는다.
  ``normalized_question`` 은 route/capability/tool-loop 입력 전용이다.
- LLM 호출/파싱이 실패하면 ``source="fallback"`` 의 보수적 결과를 반환한다.
  orchestrator 는 이 신호를 보고 기존 결정적(keyword) 경로로 내려간다 —
  provider 장애가 turn 처리 전체를 망가뜨리지 않도록.
- BIZ-452 — 출력 토큰 cap 으로 tail(`reasons` 등)이 잘린 JSON 은 conservative
  fallback 으로 내려가기 전에 truncated-tail repair 를 먼저 시도하고, repair 도
  실패하면 configured fallback backend 로 1회 재시도한다. 핵심 라우팅 판단
  (route/complexity/needs_*)이 이미 내려진 응답을 설명 필드 truncation 때문에
  버리지 않기 위함.
- 프롬프트는 코드가 아니라 ``prompts/system/turn_analysis.yaml`` 에서 관리한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from simpleclaw.agent.response_router import ResponseRoute, RouteDecision
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.llm.models import LLMRequest

logger = logging.getLogger(__name__)

# route enum 밖의 값이 오면 표준 루프로 clamp 하기 위한 허용 집합.
_ALLOWED_ROUTES = {route.value for route in ResponseRoute}

# LLM confidence 가 이 값 미만이면 되묻기(clarify) 대상으로 본다 —
# 기존 TurnFrame(BIZ-425)의 needs_clarification 임계값과 동일하게 유지.
_CLARIFY_CONFIDENCE_THRESHOLD = 0.65

# clarify 선택지 상한 — 기존 clarify UX(번호 선택)와 맞춘다.
_MAX_AMBIGUITY_OPTIONS = 4

# 최근 대화를 프롬프트에 넣을 때 메시지당 잘라낼 최대 문자 수 —
# 분석 프롬프트가 본 응답보다 커지는 역전을 막는다.
_MAX_MESSAGE_CHARS = 1200

# BIZ-452 — 설명 필드는 라우팅에 비본질적이다. 프롬프트/스키마가 짧게 쓰도록
# 지시하지만, 모델이 지시를 어겨도 하류(RouteDecision.reasons 로그 등)가 부풀지
# 않도록 파서에서 한 번 더 clamp 한다.
_MAX_CONTEXT_SUMMARY_CHARS = 240
_MAX_REASONS = 3
_MAX_REASON_CHARS = 160

# BIZ-452 — truncated-tail repair 를 신뢰하기 위해 살아 있어야 하는 핵심 필드.
# 이보다 앞에서 잘린 payload 는 라우팅 판단을 지어내지 않고 conservative
# fallback 으로 보낸다. propertyOrdering 상 route/complexity_score 는 설명 필드
# (reasons)보다 앞이므로, tail truncation 이라면 반드시 남아 있다.
_REPAIR_REQUIRED_FIELDS = ("route", "complexity_score")

# BIZ-427 — required/propertyOrdering 을 한 소스로 유지하기 위한 필드 순서.
# propertyOrdering 은 Gemini 2.0 계열에서 structured output 안정성에 필요하다.
_TURN_ANALYSIS_FIELDS = [
    "is_followup",
    "normalized_question",
    "context_summary",
    "confidence",
    "needs_clarification",
    "ambiguity_options",
    "domains",
    "intents",
    "route",
    "complexity_score",
    "needs_current_facts",
    "needs_rules",
    "needs_remaining_variables",
    "needs_calculation",
    "needs_comparison_or_conditions",
    "needs_conflict_resolution",
    "needs_impact_analysis",
    "reasons",
]

# BIZ-427 — Gemini structured output 용 TurnAnalysis JSON Schema.
# 프롬프트-only JSON 지시가 live 에서 `Unterminated string` 파싱 실패를 내는
# 문제를 schema-constrained 출력으로 차단한다. `route` 만 enum 으로 강제하고
# `domains`/`intents` 는 capability 확장성을 위해 free string array 로 둔다.
# 스키마는 문법(shape)만 보장하므로 semantic clamp 는 계속
# parse_turn_analysis_payload() 가 담당한다.
TURN_ANALYSIS_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "description": (
        "Structured analysis of one SimpleClaw user turn for normalization "
        "and routing."
    ),
    "properties": {
        "is_followup": {
            "type": "boolean",
            "description": (
                "Whether the current message depends on recent conversation "
                "context."
            ),
        },
        "normalized_question": {
            "type": "string",
            "description": (
                "Internal execution question. Preserve original meaning; add "
                "resolved context only when needed."
            ),
        },
        "context_summary": {
            "type": "string",
            "description": (
                "One short sentence (under 120 characters) about the context "
                "used, or empty if none."
            ),
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Confidence in normalization/routing decision from 0 to 1.",
        },
        "needs_clarification": {
            "type": "boolean",
            "description": (
                "True if multiple plausible contexts or missing details require "
                "asking the user."
            ),
        },
        "ambiguity_options": {
            "type": "array",
            "description": "2-4 concise options if clarification is needed; otherwise empty.",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "domains": {
            "type": "array",
            "description": (
                "Coarse domains such as sports, market, weather, entertainment, "
                "study, productivity."
            ),
            "items": {"type": "string"},
            "maxItems": 6,
        },
        "intents": {
            "type": "array",
            "description": (
                "Coarse intents such as standings, quote, weather, drama_info, "
                "realtime_lookup, scenario_analysis."
            ),
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "route": {
            "type": "string",
            "enum": [route.value for route in ResponseRoute],
            "description": "Top-level execution route.",
        },
        "complexity_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": "Overall complexity score from 0 to 10.",
        },
        "needs_current_facts": {"type": "boolean"},
        "needs_rules": {"type": "boolean"},
        "needs_remaining_variables": {"type": "boolean"},
        "needs_calculation": {"type": "boolean"},
        "needs_comparison_or_conditions": {"type": "boolean"},
        "needs_conflict_resolution": {"type": "boolean"},
        "needs_impact_analysis": {"type": "boolean"},
        # BIZ-452 — 설명 필드가 출력 cap 을 잠식해 핵심 필드 JSON 이 잘리는
        # 사고 방지: 개수를 3 으로 줄이고 짧은 bullet 문구를 지시한다.
        "reasons": {
            "type": "array",
            "description": (
                "At most 3 short bullet-style reasons, each a brief phrase."
            ),
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
    "required": _TURN_ANALYSIS_FIELDS,
    "additionalProperties": False,
    "propertyOrdering": _TURN_ANALYSIS_FIELDS,
}


@dataclass(frozen=True)
class TurnAnalysis:
    """LLM이 판단한 현재 turn의 내부 라우팅/정규화 결과.

    ``original_text`` 는 저장/감사용 원문이고, ``normalized_question`` 은
    실행 입력이다. ``source`` 는 판단 주체("llm" | "fallback")를 기록해
    orchestrator 가 결정적 fallback 경로로 내려갈지 결정하게 한다.
    """

    original_text: str
    normalized_question: str
    is_followup: bool = False
    context_summary: str = ""
    confidence: float = 1.0
    needs_clarification: bool = False
    ambiguity_options: list[str] = field(default_factory=list)
    domains: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    route: ResponseRoute = ResponseRoute.STANDARD_TOOL_LOOP
    complexity_score: int = 0
    needs_current_facts: bool = False
    needs_rules: bool = False
    needs_remaining_variables: bool = False
    needs_calculation: bool = False
    needs_comparison_or_conditions: bool = False
    needs_conflict_resolution: bool = False
    needs_impact_analysis: bool = False
    reasons: tuple[str, ...] = ()
    source: str = "llm"

    def to_route_decision(self) -> RouteDecision:
        """기존 실행 분기(orchestrator)에서 쓰는 RouteDecision으로 변환한다."""
        return RouteDecision(
            route=self.route,
            complexity_score=self.complexity_score,
            reasons=list(self.reasons) or [self.source],
            needs_current_facts=self.needs_current_facts,
            needs_rules=self.needs_rules,
            needs_remaining_variables=self.needs_remaining_variables,
            needs_calculation=self.needs_calculation,
            needs_comparison_or_conditions=self.needs_comparison_or_conditions,
            needs_conflict_resolution=self.needs_conflict_resolution,
            needs_impact_analysis=self.needs_impact_analysis,
        )


def _strip_json_fence(text: str) -> str:
    """markdown JSON fence(```json ... ```)를 제거해 순수 JSON만 남긴다."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def _clamp_float(value: object, *, default: float) -> float:
    """confidence 류 수치를 [0, 1] 범위로 보정한다. 비수치는 default."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _string_list(value: object) -> list[str]:
    """문자열 리스트만 신뢰한다 — dict/scalar 등 이형 payload 는 버린다."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def parse_turn_analysis_payload(payload: str, *, original_text: str) -> TurnAnalysis:
    """LLM JSON 응답을 TurnAnalysis로 파싱한다.

    필드 누락/이형 값은 보수적으로 sanitize 한다(route clamp, confidence
    clamp, 리스트 정제). JSON 자체가 파싱 불가할 때만 ValueError 를 던진다 —
    호출자(analyze_turn_with_llm)가 truncated-tail repair 또는 conservative
    fallback 으로 전환한다.
    """
    data = json.loads(_strip_json_fence(payload))
    if not isinstance(data, dict):
        raise ValueError("turn analysis payload must be a JSON object")
    return _build_turn_analysis(data, original_text=original_text)


def _build_turn_analysis(data: dict, *, original_text: str) -> TurnAnalysis:
    """파싱/repair 된 dict 를 sanitize 해 TurnAnalysis 로 조립한다."""
    route_value = str(data.get("route") or ResponseRoute.STANDARD_TOOL_LOOP.value)
    route = (
        ResponseRoute(route_value)
        if route_value in _ALLOWED_ROUTES
        else ResponseRoute.STANDARD_TOOL_LOOP
    )

    # 정규화 질문이 비면 원문으로 되돌린다 — 실행 입력이 비는 사고 방지.
    normalized = str(data.get("normalized_question") or original_text).strip() or original_text
    confidence = _clamp_float(data.get("confidence"), default=0.5)

    return TurnAnalysis(
        original_text=original_text,
        normalized_question=normalized,
        is_followup=bool(data.get("is_followup", False)),
        context_summary=str(data.get("context_summary") or "")[
            :_MAX_CONTEXT_SUMMARY_CHARS
        ],
        confidence=confidence,
        # LLM이 명시하지 않아도 저신뢰면 되묻기 대상으로 승격한다.
        needs_clarification=bool(data.get("needs_clarification", False))
        or confidence < _CLARIFY_CONFIDENCE_THRESHOLD,
        ambiguity_options=_string_list(data.get("ambiguity_options"))[
            :_MAX_AMBIGUITY_OPTIONS
        ],
        domains=tuple(_string_list(data.get("domains"))),
        intents=tuple(_string_list(data.get("intents"))),
        route=route,
        complexity_score=max(0, min(10, _int(data.get("complexity_score"), 0))),
        needs_current_facts=bool(data.get("needs_current_facts", False)),
        needs_rules=bool(data.get("needs_rules", False)),
        needs_remaining_variables=bool(data.get("needs_remaining_variables", False)),
        needs_calculation=bool(data.get("needs_calculation", False)),
        needs_comparison_or_conditions=bool(
            data.get("needs_comparison_or_conditions", False)
        ),
        needs_conflict_resolution=bool(data.get("needs_conflict_resolution", False)),
        needs_impact_analysis=bool(data.get("needs_impact_analysis", False)),
        reasons=tuple(
            reason[:_MAX_REASON_CHARS]
            for reason in _string_list(data.get("reasons"))[:_MAX_REASONS]
        ),
        source="llm",
    )


def _repair_truncated_json_object(text: str) -> dict | None:
    """출력 토큰 cap 으로 tail 이 잘린 JSON object 를 최소 복구한다.

    문자열/escape/컨테이너 중첩 상태를 추적하며 한 번 스캔해 "값이 완결된
    지점"들을 기록한 뒤, 가장 뒤쪽의 완결 지점까지 자르고 열린 컨테이너를
    닫아 파싱을 재시도한다. 잘린 문자열·키·스칼라 조각은 통째로 버려진다 —
    앞쪽에 이미 완결된 필드만 보존하는 보수적 복구다.

    Returns:
        복구된 dict. object 로 시작하지 않거나 어떤 완결 지점에서도 파싱이
        되지 않으면 None — 호출자가 fallback 여부를 결정한다.
    """
    if not text or text[0] != "{":
        return None

    stack: list[str] = []  # 열린 컨테이너 스냅샷용 ("{" 또는 "[")
    # (자를 위치(exclusive), 그 시점의 열린 컨테이너 스냅샷)
    candidates: list[tuple[int, tuple[str, ...]]] = []
    in_string = False
    escape = False
    string_is_value = False
    # object 안에서 다음 문자열이 key 인지 value 인지 구분하기 위한 상태 —
    # key 직후에 자르면 `{"key"` 처럼 복구 불가능한 prefix 가 되기 때문.
    expect_value = False
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                if string_is_value:
                    candidates.append((i + 1, tuple(stack)))
                    expect_value = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            escape = False
            string_is_value = expect_value or bool(stack and stack[-1] == "[")
            i += 1
            continue
        if ch == ":":
            expect_value = True
            i += 1
            continue
        if ch == ",":
            # object 의 `,` 다음은 key, array 의 `,` 다음은 value 다.
            expect_value = bool(stack and stack[-1] == "[")
            i += 1
            continue
        if ch in "{[":
            stack.append(ch)
            expect_value = ch == "["
            i += 1
            continue
        if ch in "}]":
            if not stack:
                return None  # 여는 짝 없는 닫힘 — truncation 이 아닌 손상.
            stack.pop()
            candidates.append((i + 1, tuple(stack)))
            expect_value = False
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        # 숫자/true/false/null 스칼라 토큰 — 뒤에 구분자가 있어야 완결로 본다.
        # 텍스트 끝에서 끊긴 토큰(예: `tru`, `0.8`)은 잘렸을 수 있으므로 버린다.
        token_end = i
        while token_end < length and text[token_end] not in ",]}" and (
            not text[token_end].isspace()
        ):
            token_end += 1
        if token_end < length:
            try:
                json.loads(text[i:token_end])
            except ValueError:
                pass
            else:
                candidates.append((token_end, tuple(stack)))
        expect_value = False
        i = token_end

    # 뒤쪽 완결 지점부터 시도 — 가장 많은 필드를 보존하는 복구를 우선한다.
    for cut, open_containers in reversed(candidates):
        closers = "".join(
            "}" if container == "{" else "]"
            for container in reversed(open_containers)
        )
        try:
            data = json.loads(text[:cut] + closers)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def repair_turn_analysis_payload(
    payload: str, *, original_text: str
) -> TurnAnalysis | None:
    """잘린 TurnAnalysis JSON 을 복구해 핵심 라우팅 판단을 보존한다 (BIZ-452).

    출력 토큰 cap 으로 tail(`reasons` 등)이 잘렸어도 핵심 필드(route,
    complexity_score, confidence, domains/intents, needs_*)가 완결돼 있으면
    그 판단을 그대로 살린다. 잘린 `reasons` 항목은 버려지고 긴 설명 필드는
    파서가 clamp 한다.

    Returns:
        복구된 TurnAnalysis(``source="llm"``). 핵심 필드 이전에서 잘렸거나
        object 복구가 불가능하면 None — 라우팅 판단을 지어내지 않는다.
    """
    data = _repair_truncated_json_object(_strip_json_fence(payload))
    if not isinstance(data, dict):
        return None
    if any(field_name not in data for field_name in _REPAIR_REQUIRED_FIELDS):
        return None
    return _build_turn_analysis(data, original_text=original_text)


def _format_recent_messages(recent_messages: list[dict], *, limit: int) -> str:
    """최근 대화를 ``role: content`` 한 줄 포맷으로 직렬화한다."""
    selected = list(recent_messages or [])[-limit:]
    lines: list[str] = []
    for msg in selected:
        role = str(msg.get("role") or "unknown")
        content = " ".join(str(msg.get("content") or "").split())
        if content:
            lines.append(f"{role}: {content[:_MAX_MESSAGE_CHARS]}")
    return "\n".join(lines)


def _fallback_analysis(original: str) -> TurnAnalysis:
    """LLM 실패 시의 보수적 결과 — 원문 유지 + 표준 루프."""
    return TurnAnalysis(
        original_text=original,
        normalized_question=original,
        confidence=0.0,
        route=ResponseRoute.STANDARD_TOOL_LOOP,
        reasons=("turn_analysis_fallback",),
        source="fallback",
    )


async def analyze_turn_with_llm(
    text: str,
    *,
    recent_messages: list[dict] | None,
    router,
    max_tokens: int = 2048,
    max_recent_messages: int = 12,
    structured_output: bool = True,
    reasoning: dict | None = None,
) -> TurnAnalysis:
    """LLM으로 follow-up/정규화/clarify/복잡도/라우팅을 한 번에 판단한다.

    Args:
        text: 사용자 원문 발화. 저장은 원문 그대로, 실행은 결과의
            ``normalized_question`` 을 쓴다.
        recent_messages: ``{"role": ..., "content": ...}`` 형태의 최근 대화.
        router: ``LLMRouter`` (또는 동일 ``send`` 시그니처의 대역).
        Backend selection and provider retry are owned by the ``turn_analysis``
        route in :class:`LLMRouter`.
        max_tokens: 분석 응답 토큰 상한 — 레이턴시/비용 제어용.
        max_recent_messages: 프롬프트에 포함할 최근 메시지 수 상한.
        structured_output: True(기본)면 provider structured output 으로 schema
            준수 JSON 을 강제한다 (BIZ-427/450). False 는 프롬프트-only JSON
            지시로 동작하는 운영 escape hatch.
        reasoning: provider-neutral reasoning hint (BIZ-453). ``enabled`` 가
            참일 때만 요청에 실린다. 미지원 provider 는 조용히 무시하므로
            retry backend 가 다른 provider 여도 안전하다.

    Returns:
        LLM 판단 결과(:class:`TurnAnalysis`, ``source="llm"``). 호출/파싱/
        repair/재시도가 모두 실패하면 원문 보존 + 표준 루프의 보수적 결과
        (``source="fallback"``).
    """
    original = text or ""
    recent_block = _format_recent_messages(
        recent_messages or [], limit=max_recent_messages
    )
    user_message = (
        "## Recent conversation\n"
        f"{recent_block or '(none)'}\n\n"
        "## Current user message\n"
        f"{original}"
    )

    def _build_request() -> LLMRequest:
        request = LLMRequest(
            system_prompt=load_system_prompt("turn_analysis").system_prompt,
            user_message=user_message,
            route_name="turn_analysis",
            max_tokens=max_tokens,
        )
        if structured_output:
            # BIZ-427 — schema-constrained JSON 출력을 provider 에 요구한다.
            # 미지원 provider 는 LLMProviderError 를 던지고 아래 fallback 으로
            # 안전하게 내려간다.
            request.response_mime_type = "application/json"
            request.response_schema = TURN_ANALYSIS_RESPONSE_SCHEMA
            request.require_structured_output = True
        if isinstance(reasoning, dict) and reasoning.get("enabled"):
            # BIZ-453 — reasoning hint 는 켜져 있을 때만 요청에 싣는다.
            request.reasoning = dict(reasoning)
        return request

    async def _attempt() -> tuple[TurnAnalysis | None, dict]:
        """한 backend 로 분석을 시도하고 (결과, 안전 진단 메타데이터)를 반환한다.

        진단에는 예외 타입/raw 길이/finish_reason/repair 상태만 담는다 —
        provider 예외 메시지와 raw 전문에는 사용자 발화가 그대로 포함될 수
        있어 원문 문자열은 절대 밖으로 내보내지 않는다 (BIZ-430).
        """
        diag: dict = {
            "backend": "turn_analysis",
            "error_type": None,
            "raw_len": 0,
            "finish_reason": None,
            "repair_status": "none",
        }
        response = None
        try:
            response = await router.send(_build_request())
            diag["raw_len"] = len(getattr(response, "text", "") or "")
            finish_reason = getattr(response, "finish_reason", None)
            if isinstance(finish_reason, str):
                diag["finish_reason"] = finish_reason
            return (
                parse_turn_analysis_payload(response.text, original_text=original),
                diag,
            )
        except Exception as exc:  # noqa: BLE001 — 분석 실패는 turn 을 막지 않는다.
            diag["error_type"] = type(exc).__name__
            # 파싱 실패(ValueError/JSONDecodeError)만 repair 대상 — provider
            # 예외는 응답 자체가 없으므로 복구할 payload 가 없다.
            if isinstance(exc, ValueError) and response is not None:
                repaired = repair_turn_analysis_payload(
                    response.text, original_text=original
                )
                if repaired is not None:
                    diag["repair_status"] = "repaired"
                    return repaired, diag
                diag["repair_status"] = "failed"
            return None, diag

    analysis, diag = await _attempt()
    if analysis is not None:
        if diag["repair_status"] == "repaired":
            logger.warning(
                "Turn analysis payload truncated; repaired tail kept core "
                "routing decision (error_type=%s structured=%s raw_len=%d "
                "finish_reason=%s backend=%s repair_status=repaired)",
                diag["error_type"],
                structured_output,
                diag["raw_len"],
                diag["finish_reason"],
                diag["backend"],
            )
        return analysis

    logger.warning(
        "Turn analysis structured output failed; using conservative "
        "fallback (error_type=%s structured=%s raw_len=%d finish_reason=%s "
        "backend=%s repair_status=%s)",
        diag["error_type"],
        structured_output,
        diag["raw_len"],
        diag["finish_reason"],
        diag["backend"],
        diag["repair_status"],
    )
    return _fallback_analysis(original)
