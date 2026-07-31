"""Typed intent/domain metadata를 capability 선언과 비교하는 compatibility adapter.

안전 원칙:
- `read_only=true` + `side_effects=false` 로 **명시 선언된** 자산만 후보.
- 부작용 있는 recipe/skill 은 어떤 경우에도 자동 실행 후보로 반환하지 않는다.
- metadata 없는 기존 자산은 후보가 되지 않고, 기존 asset selector 경로가
  그대로 fallback 으로 동작한다.
- 사용자 텍스트에서 intent/domain을 추론하지 않는다.
- explicit metadata가 없으면 fail closed로 ``None``을 반환한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition

logger = logging.getLogger(__name__)

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


def select_capability(
    normalized_question: str,
    *,
    skills: list[SkillDefinition],
    recipes: list[RecipeDefinition],
    explicit_intents: tuple[str, ...] | list[str] | None = None,
    explicit_domains: tuple[str, ...] | list[str] | None = None,
) -> CapabilityDecision | None:
    """정규화 질문에 맞는 read-only 자동 실행 후보를 하나 고른다.

    ``explicit_intents``/``explicit_domains``만 사용한다. 사용자 텍스트는
    compatibility signature로만 남고 의미 판단에는 사용하지 않는다.

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
    del normalized_question
    question_intents = set(explicit_intents or ())
    if not question_intents:
        return None
    question_domains = set(explicit_domains or ())

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
