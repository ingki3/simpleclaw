"""Fallback response route classifier for SimpleClaw turns.

BIZ-426 — 일반 turn 의 primary route 판단은 ``agent.turn_analysis.enabled``
가 켜져 있을 때 LLM TurnAnalysis(``simpleclaw.agent.turn_analysis``)가
수행한다. 이 모듈은 provider 장애/turn analysis 비활성 시의 결정적
fallback 과 route 계약 단위 테스트를 위해 유지된다 — 신규 keyword cue
확장으로 라우팅 문제를 풀지 말 것(스킬/프롬프트/LLM 판단 우선).

The router is intentionally conservative. It sends only structurally complex
factual/scenario questions into ComplexFactWorkflow. Simple chat, normal tool
use, and single current-fact lookup stay on the existing ToolLoopRunner path.

BIZ-394 — 라우터는 더 이상 특정 사건 키워드(예: "월드컵")만 보지 않는다. 대신
"현재성/도메인 이벤트(상장·IPO·실적 등) + 분석/영향(영향·전망·조사 등)" 같은
*구조적 cue 조합*으로 현재성·영향 분석 질문을 잡아낸다. 또한 답변 근거로 쓰이는
Agent Study Wiki context 가 stale 하거나 confidence 가 낮으면, 그 자체를 신호로 삼아
현재 사실 재조회(at-least guarded) 를 강제한다(배경지식만으로 단정하지 않도록).

BIZ-425 — 일반 사용자 turn 의 입력 텍스트는 orchestrator 가 TurnFrame 으로
맥락 복원한 ``normalized_question`` 이라는 전제를 둔다(원문은 DB 저장 전용).
도메인별 route override(예: sports standings → guarded)는 여기 추가하지 않는다
— 케이스별 처리는 skill/recipe capability metadata 와 capability router 가
담당하고, 이 라우터는 구조적 cue 만 본다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ResponseRoute(str, Enum):
    """Top-level execution paths for ordinary user turns."""

    STANDARD_TOOL_LOOP = "standard_tool_loop"
    CURRENT_FACT_GUARDED_LOOP = "current_fact_guarded_loop"
    COMPLEX_FACT_WORKFLOW = "complex_fact_workflow"


@dataclass(frozen=True)
class RouteDecision:
    """Structured route decision used by AgentOrchestrator."""

    route: ResponseRoute
    complexity_score: int
    reasons: list[str] = field(default_factory=list)
    needs_current_facts: bool = False
    needs_rules: bool = False
    needs_remaining_variables: bool = False
    needs_calculation: bool = False
    needs_comparison_or_conditions: bool = False
    needs_conflict_resolution: bool = False
    # 도메인 이벤트(상장/실적 등) + 분석/영향(영향/전망 등) 구조적 조합 여부.
    needs_impact_analysis: bool = False
    # 답변 근거 study context 가 stale/저신뢰라서 현재 사실 재조회를 강제해야 하는지.
    study_context_unreliable: bool = False


_SMALLTALK = ("안녕", "고마워", "감사", "hello", "hi", "thanks")
_CURRENT_FACT_CUES = (
    "오늘",
    "현재",
    "지금",
    "실시간",
    "최신",
    "방금",
    "마감",
    "결과",
    "예보",
)
_DOMAIN_CURRENT_CUES = (
    "날씨",
    "주가",
    "환율",
    "뉴스",
    "속보",
    "경기",
    "스코어",
    "코스피",
    "코스닥",
    "순위",
    "승점",
)
_RULE_CUES = (
    "규칙",
    "조건",
    "기준",
    "적용",
    "자격",
    "진출",
    "탈락",
    "우승",
    "정책",
    "요건",
    "16강",
    "8강",
    "티켓",
)
_REMAINING_VARIABLE_CUES = (
    "남은",
    "앞으로",
    "경우의 수",
    "가능성",
    "시나리오",
    "어떻게 되면",
    "아직",
    "될 수",
)
_CALCULATION_CUES = (
    "계산",
    "확률",
    "몇",
    "순위",
    "비교",
    "경우의 수",
    "점수",
    "승점",
    "차이",
    "얼마",
)
_CONDITION_CUES = (
    "이면",
    "라면",
    "조건별",
    "비교",
    "대상",
    "그룹",
    "표",
    "랭킹",
    "내 상황",
)
# 도메인 이벤트 cue — 특정 사건명이 아니라 "현재성을 띠는 사건 유형"을 가리킨다.
# 기업·시장 이벤트(상장/IPO/실적 등)는 그 자체로 시점에 민감한 외부 사실이다.
_DOMAIN_EVENT_CUES = (
    "상장",
    "ipo",
    "공모",
    "기업가치",
    "시총",
    "sec",
    "실적",
    "가이던스",
    "인수",
    "합병",
    "증자",
    "증시",
    "시장 반응",
)
# 분석/영향 cue — "단순 사실 조회"가 아니라 "왜·영향·전망을 따져 달라"는 신호.
# 단독으로는 complex 로 올리지 않고, 도메인 이벤트/현재성과 조합될 때만 가중한다.
_ANALYSIS_IMPACT_CUES = (
    "영향",
    "파급",
    "전망",
    "리스크",
    "기회",
    "시장 반응",
    "투자 판단",
    "조사",
    "분석",
    "왜",
    "원인",
    "배경",
)

# study context 블록에서 confidence 수치를 뽑는 정규식("Confidence: 0.42").
_STUDY_CONFIDENCE_RE = re.compile(r"confidence\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
# context_retrieval 이 stale 한 study context 에 심는 마커(자유 텍스트와 무관히 파싱).
_STUDY_STALE_MARKERS = ("freshness: stale", "freshness=stale", "study_context:stale")
# study confidence 가 이 값 미만이면 저신뢰로 간주(배경지식만으로 단정 금지).
_DEFAULT_STUDY_MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class StudyContextAssessment:
    """주입된 Agent Study context 의 신선도/신뢰도 요약."""

    has_context: bool = False
    min_confidence: float | None = None
    is_stale: bool = False
    is_low_confidence: bool = False

    @property
    def is_unreliable(self) -> bool:
        """배경지식만으로 답하면 위험한 상태(stale 또는 저신뢰)인지."""
        return self.has_context and (self.is_stale or self.is_low_confidence)


def assess_study_context(
    study_context: str,
    *,
    min_confidence: float = _DEFAULT_STUDY_MIN_CONFIDENCE,
) -> StudyContextAssessment:
    """주입된 study context 블록의 stale/저신뢰 여부를 보수적으로 판정한다.

    라우터는 시계(clock)를 직접 들지 않는다. 신선도 비교(updated_at vs now)는 시계를
    가진 context_retrieval 이 수행하고, 그 결과를 ``Freshness: stale`` 마커로 블록에
    심는다. 라우터는 그 마커와 ``Confidence:`` 수치만 읽어 결정적으로 판정한다.

    Args:
        study_context: ``StudyRetriever`` 가 만든(그리고 context_retrieval 이 신선도
            마커를 보강한) "## Agent Study Context" 블록. 비어 있으면 영향 없음.
        min_confidence: 이 값 미만의 최저 confidence 면 저신뢰로 본다.

    Returns:
        :class:`StudyContextAssessment`.
    """
    text = (study_context or "").strip()
    if not text:
        return StudyContextAssessment()

    lowered = text.lower()
    is_stale = any(marker in lowered for marker in _STUDY_STALE_MARKERS)

    confidences = [float(m) for m in _STUDY_CONFIDENCE_RE.findall(text)]
    min_conf = min(confidences) if confidences else None
    is_low_conf = min_conf is not None and min_conf < float(min_confidence)

    return StudyContextAssessment(
        has_context=True,
        min_confidence=min_conf,
        is_stale=is_stale,
        is_low_confidence=is_low_conf,
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def classify_response_route(
    text: str,
    prior_context: str = "",
    *,
    route_threshold: int = 3,
    study_context: str = "",
    study_min_confidence: float = _DEFAULT_STUDY_MIN_CONFIDENCE,
) -> RouteDecision:
    """Classify a turn into a cheap execution path.

    This is not intended to be perfect. It should avoid false positives for the
    heavy workflow while catching structurally complex questions that require
    several independent evidence slots before answering.

    Args:
        text: 현재 사용자 발화.
        prior_context: 직전 대화 맥락(현재성/규칙 cue 보강용).
        route_threshold: complex 승격 점수 임계값.
        study_context: 이번 답변 근거로 주입될 Agent Study Wiki context 블록(있으면).
            stale/저신뢰면 현재 사실 재조회(at-least guarded)를 강제하는 신호로 쓴다.
        study_min_confidence: study confidence 저신뢰 판정 임계값.
    """

    normalized = " ".join((text or "").split())
    if not normalized:
        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["empty"])

    if len(normalized) <= 20 and _contains_any(normalized, _SMALLTALK):
        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["smalltalk"])

    combined = f"{prior_context[-1200:]}\n{normalized}" if prior_context else normalized
    needs_remaining = _contains_any(combined, _REMAINING_VARIABLE_CUES)
    needs_calculation = _contains_any(combined, _CALCULATION_CUES)
    needs_conditions = _contains_any(combined, _CONDITION_CUES)
    needs_rules = _contains_any(combined, _RULE_CUES)

    # Scenario wording generally implies an external current state and governing
    # rule even if the user does not say "현재" or "규칙" explicitly.
    if needs_remaining and (needs_calculation or needs_conditions):
        needs_rules = True
        needs_conditions = True

    needs_current = _contains_any(combined, _CURRENT_FACT_CUES) or _contains_any(
        combined, _DOMAIN_CURRENT_CUES
    )
    if needs_remaining:
        needs_current = True

    # 구조적 영향 분석: 도메인 이벤트(상장/실적/증시 등) 또는 현재성 cue 가 분석/영향
    # cue(영향/전망/조사 등)와 함께 나타나면, 특정 사건 키워드 없이도 "현재 사실을
    # 근거로 영향을 따져야 하는" 질문으로 본다. 분석 cue 단독은 가중하지 않는다.
    has_domain_event = _contains_any(combined, _DOMAIN_EVENT_CUES)
    has_analysis_impact = _contains_any(combined, _ANALYSIS_IMPACT_CUES)
    needs_impact_analysis = has_analysis_impact and (has_domain_event or needs_current)
    if needs_impact_analysis:
        # 영향 분석은 항상 최신 사실을 근거로 해야 한다(배경지식만으로 단정 금지).
        needs_current = True

    # Freshness/confidence gate — 답변 근거로 쓰일 study context 가 stale/저신뢰면,
    # 현재성/영향/시나리오성 질문에 한해 현재 사실 재조회를 강제한다(standard 금지).
    study = assess_study_context(study_context, min_confidence=study_min_confidence)
    turn_is_freshness_sensitive = (
        needs_current or needs_impact_analysis or needs_remaining or needs_rules
    )
    study_forces_lookup = study.is_unreliable and turn_is_freshness_sensitive
    if study_forces_lookup:
        needs_current = True

    reasons: list[str] = []
    score = 0
    for flag, reason in [
        (needs_current, "current_facts"),
        (needs_rules, "rules"),
        (needs_remaining, "remaining_variables"),
        (needs_calculation, "calculation"),
        (needs_conditions, "conditions_or_comparison"),
        (needs_impact_analysis, "impact_analysis"),
    ]:
        if flag:
            score += 1
            reasons.append(reason)

    if study_forces_lookup:
        score += 1
        reasons.append("stale_study_context" if study.is_stale else "low_confidence_study_context")

    threshold = max(1, int(route_threshold))
    complex_shape = score >= threshold or (
        needs_rules and (needs_remaining or needs_conditions) and not _is_single_current_fact(combined)
    )
    if complex_shape:
        return RouteDecision(
            ResponseRoute.COMPLEX_FACT_WORKFLOW,
            score,
            reasons,
            needs_current_facts=needs_current,
            needs_rules=needs_rules,
            needs_remaining_variables=needs_remaining,
            needs_calculation=needs_calculation,
            needs_comparison_or_conditions=needs_conditions,
            needs_conflict_resolution=needs_current and (needs_rules or needs_remaining),
            needs_impact_analysis=needs_impact_analysis,
            study_context_unreliable=study_forces_lookup,
        )

    if needs_current:
        return RouteDecision(
            ResponseRoute.CURRENT_FACT_GUARDED_LOOP,
            score,
            reasons or ["current_fact_guard"],
            needs_current_facts=True,
            needs_impact_analysis=needs_impact_analysis,
            study_context_unreliable=study_forces_lookup,
        )

    return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, score, reasons or ["default"])


def _is_single_current_fact(text: str) -> bool:
    """Return True for simple quote/weather/news lookups that should stay cheap."""

    simple_lookup_cues = ("날씨", "주가", "환율", "뉴스", "속보", "얼마")
    return _contains_any(text, simple_lookup_cues) and not _contains_any(
        text,
        _REMAINING_VARIABLE_CUES + _RULE_CUES + _CONDITION_CUES,
    )
