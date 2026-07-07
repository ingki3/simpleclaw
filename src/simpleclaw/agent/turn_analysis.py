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
    호출자(analyze_turn_with_llm)가 conservative fallback 으로 전환한다.
    """
    data = json.loads(_strip_json_fence(payload))
    if not isinstance(data, dict):
        raise ValueError("turn analysis payload must be a JSON object")

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
        context_summary=str(data.get("context_summary") or ""),
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
        reasons=tuple(_string_list(data.get("reasons"))),
        source="llm",
    )


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
    backend_name: str | None = None,
    max_tokens: int = 512,
    max_recent_messages: int = 12,
) -> TurnAnalysis:
    """LLM으로 follow-up/정규화/clarify/복잡도/라우팅을 한 번에 판단한다.

    Args:
        text: 사용자 원문 발화. 저장은 원문 그대로, 실행은 결과의
            ``normalized_question`` 을 쓴다.
        recent_messages: ``{"role": ..., "content": ...}`` 형태의 최근 대화.
        router: ``LLMRouter`` (또는 동일 ``send`` 시그니처의 대역).
        backend_name: 분석 전용 backend. None 이면 라우터 기본 backend.
        max_tokens: 분석 응답 토큰 상한 — 레이턴시/비용 제어용.
        max_recent_messages: 프롬프트에 포함할 최근 메시지 수 상한.

    Returns:
        LLM 판단 결과(:class:`TurnAnalysis`, ``source="llm"``). 호출/파싱
        실패 시 원문 보존 + 표준 루프의 보수적 결과(``source="fallback"``).
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
    try:
        request = LLMRequest(
            system_prompt=load_system_prompt("turn_analysis").system_prompt,
            user_message=user_message,
            backend_name=backend_name,
            max_tokens=max_tokens,
        )
        response = await router.send(request)
        return parse_turn_analysis_payload(response.text, original_text=original)
    except Exception as exc:  # noqa: BLE001 — 분석 실패는 turn 을 막지 않는다.
        logger.warning("Turn analysis LLM failed; using conservative fallback: %s", exc)
        return _fallback_analysis(original)
