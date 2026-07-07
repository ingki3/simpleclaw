"""정규화 질문과 capability metadata 를 비교하는 read-only capability router.

BIZ-425 — `sports standings → CURRENT_FACT_GUARDED_LOOP` 같은 도메인별 Python
route override 를 라우터에 계속 추가하면 분기 폭증/회귀가 생긴다. 대신 이
모듈은 TurnFrame 이 만든 `normalized_question` 에서 **일반적인 의도/도메인
cue** 만 추론하고, 그 결과를 skill/recipe 가 스스로 선언한 capability
metadata(intents/domains/read_only/side_effects)와 대조해 자동 실행 후보를
고른다. 어떤 route 로 갈지는 여전히 response_router 가 결정한다 — 여기서는
"이 질문을 read-only 자산으로 직접 해결할 수 있는가"만 판단한다.

안전 원칙:
- `read_only=true` + `side_effects=false` 로 **명시 선언된** 자산만 후보.
- 부작용 있는 recipe/skill 은 어떤 경우에도 자동 실행 후보로 반환하지 않는다.
- metadata 없는 기존 자산은 후보가 되지 않고, 기존 asset selector 경로가
  그대로 fallback 으로 동작한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition

logger = logging.getLogger(__name__)

# 질문 텍스트 → 의도 추론 cue. 특정 도메인 자산을 가리키지 않는 일반 표현만
# 담는다 — 실제 매칭은 자산이 선언한 intents 와의 교집합으로만 성립한다.
_INTENT_CUES: dict[str, tuple[str, ...]] = {
    "standings": ("순위", "순위표", "랭킹", "standings"),
    "current_result": ("결과", "스코어", "어떻게 됐", "어떻게 되었", "이겼", "졌"),
    "schedule": ("일정", "중계", "방송", "몇 시", "언제 해"),
    "quote": ("주가", "환율", "시세", "지수", "가격"),
    "weather": ("날씨", "기온", "강수", "예보", "미세먼지"),
    "news": ("뉴스", "속보", "헤드라인", "기사"),
    "daily_report": ("리포트", "보고서", "브리핑", "데일리"),
    "realtime_lookup": ("현재", "지금", "실시간", "최신"),
}

# 질문 텍스트 → 도메인 힌트 cue. selector 힌트 용도일 뿐 route 를 강제하지
# 않는다. 자산이 domains 를 선언한 경우 오매칭 방지 필터로만 쓰인다.
_DOMAIN_CUES: dict[str, tuple[str, ...]] = {
    "sports": ("야구", "축구", "농구", "배구", "경기", "리그", "구단", "kbo", "mlb", "epl", "스포츠", "프로야구"),
    "market": ("주식", "증시", "주가", "코스피", "코스닥", "나스닥", "환율", "종목", "etf", "시장"),
    "weather": ("날씨", "기온", "강수", "예보", "미세먼지"),
    "news": ("뉴스", "속보", "기사", "헤드라인"),
    "study": ("공부", "학습", "스터디", "study"),
}

# realtime_lookup 은 시점 cue 일 뿐 단독으로는 자산 의도를 특정하지 못한다.
# 이 의도"만" 겹치는 매칭은 과매칭이므로 제외한다.
_WEAK_INTENTS = frozenset({"realtime_lookup"})


@dataclass(frozen=True)
class CapabilityDecision:
    """capability router 가 고른 단일 자동 실행 후보."""

    asset_type: str  # "skill" | "recipe"
    asset_name: str
    matched_intents: tuple[str, ...] = ()
    matched_domains: tuple[str, ...] = ()
    safe_to_auto_execute: bool = False
    score: int = 0
    reasons: tuple[str, ...] = ()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def infer_intents(text: str) -> tuple[str, ...]:
    """질문 텍스트에서 coarse 의도들을 추론한다 (도메인 무관 cue 기반)."""
    normalized = " ".join((text or "").split())
    if not normalized:
        return ()
    return tuple(
        intent
        for intent, cues in _INTENT_CUES.items()
        if _contains_any(normalized, cues)
    )


def infer_domains(text: str) -> tuple[str, ...]:
    """질문 텍스트에서 도메인 힌트들을 추론한다 (selector 힌트 전용)."""
    normalized = " ".join((text or "").split())
    if not normalized:
        return ()
    return tuple(
        domain
        for domain, cues in _DOMAIN_CUES.items()
        if _contains_any(normalized, cues)
    )


def select_capability(
    normalized_question: str,
    *,
    skills: list[SkillDefinition],
    recipes: list[RecipeDefinition],
    explicit_intents: tuple[str, ...] | list[str] | None = None,
    explicit_domains: tuple[str, ...] | list[str] | None = None,
) -> CapabilityDecision | None:
    """정규화 질문에 맞는 read-only 자동 실행 후보를 하나 고른다.

    BIZ-426 — LLM TurnAnalysis 가 제공한 ``explicit_intents``/``explicit_domains``
    가 있으면 그것을 1순위로 쓴다. keyword cue 추론(:func:`infer_intents`,
    :func:`infer_domains`)은 LLM metadata 가 없을 때(분석 비활성/실패)의
    호환용 fallback 이다.

    매칭 규칙:
    - 자산의 선언 intents 와 질문 intents(LLM 제공 또는 keyword 추론)의
      교집합이 있어야 한다. 단, 시점 cue(realtime_lookup)만 겹치는 매칭은
      제외한다.
    - 자산이 domains 를 선언했고 질문에서도 도메인이 특정됐는데 교집합이
      없으면 오매칭으로 보고 제외한다.
    - `safe_for_auto_execution` (명시 선언된 read-only + 무부수효과) 자산만
      후보로 반환한다. 부작용 자산은 점수와 무관하게 None.

    Returns:
        가장 높은 점수의 :class:`CapabilityDecision`, 후보가 없으면 None.
    """
    question_intents = set(explicit_intents or infer_intents(normalized_question))
    if not question_intents:
        return None
    question_domains = set(explicit_domains or infer_domains(normalized_question))

    assets: list[tuple[str, str, object]] = [
        ("skill", skill.name, skill) for skill in skills
    ]
    assets.extend(("recipe", recipe.name, recipe) for recipe in recipes)

    best: CapabilityDecision | None = None
    for asset_type, asset_name, asset in assets:
        capability = getattr(asset, "capability", None)
        if capability is None or not capability.safe_for_auto_execution:
            # 부작용/미선언 자산은 자동 실행 후보 자체가 되지 않는다.
            continue

        matched_intents = set(capability.intents) & question_intents
        if not matched_intents or matched_intents <= _WEAK_INTENTS:
            continue

        matched_domains = set(capability.domains) & question_domains
        if capability.domains and question_domains and not matched_domains:
            # 자산과 질문 양쪽 모두 도메인이 특정됐는데 서로 다르면 오매칭.
            continue

        score = 2 * len(matched_intents) + len(matched_domains)
        if capability.freshness_sensitive and "realtime_lookup" in question_intents:
            score += 1
        if best is None or score > best.score:
            best = CapabilityDecision(
                asset_type=asset_type,
                asset_name=asset_name,
                matched_intents=tuple(sorted(matched_intents)),
                matched_domains=tuple(sorted(matched_domains)),
                safe_to_auto_execute=True,
                score=score,
                reasons=(
                    f"intents={sorted(matched_intents)}",
                    f"domains={sorted(matched_domains)}",
                ),
            )

    if best is not None:
        logger.debug(
            "Capability match: %s:%s score=%d intents=%s domains=%s",
            best.asset_type, best.asset_name, best.score,
            best.matched_intents, best.matched_domains,
        )
    return best
