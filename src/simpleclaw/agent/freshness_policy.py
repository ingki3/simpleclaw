"""Planner payload와 분리된 deterministic freshness policy."""

from __future__ import annotations

from collections.abc import Iterable

from simpleclaw.agent.planner_catalog import PlannerAsset
from simpleclaw.agent.turn_plan import UnifiedTurnPlan

# Freshness 예외는 planner의 boolean 자기선언이 아니라 이 좁은 typed policy로만
# 부여한다. 알 수 없는 intent는 current fact일 수 있으므로 fail-closed한다.
_FRESHNESS_OPTIONAL_INTENTS = frozenset({"definition"})
_CURRENT_FACT_INTENTS = frozenset({
    "current_fact",
    "current_result",
    "current_weather",
    "live_score",
    "market_quote",
    "quote",
    "realtime_lookup",
})


def freshness_is_required(
    plan: UnifiedTurnPlan,
    *,
    assets: Iterable[PlannerAsset] = (),
) -> bool:
    """Return the trusted policy decision for freshness validation."""
    intents = frozenset((*plan.intents, *plan.fact_check.intents))
    if plan.fact_check.freshness_required:
        return True
    if intents & _CURRENT_FACT_INTENTS:
        return True
    return any(asset.freshness_sensitive for asset in assets)


def freshness_optional_claims(plan: UnifiedTurnPlan) -> tuple[str, ...]:
    """Return claims covered by the narrow non-current-fact exception."""
    intents = frozenset((*plan.intents, *plan.fact_check.intents))
    if (
        plan.fact_check.freshness_required
        or not intents
        or not intents.issubset(_FRESHNESS_OPTIONAL_INTENTS)
    ):
        return ()
    return plan.fact_check.required_claims
