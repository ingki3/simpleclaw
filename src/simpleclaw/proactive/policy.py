"""TPO(Time/Place/Occasion) proactive 발송 정책 엔진.

이 엔진은 후보 자체의 confidence/urgency와 사용자 접점 제한(quiet hours, daily
budget, cooldown)을 한 곳에서 평가한다. detector는 이 정책을 모르고 후보만 만들며,
presenter는 구조화된 PolicyDecision만 보고 발송/보류/억제를 수행한다.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from simpleclaw.proactive.models import (
    OpportunityType,
    PolicyDecision,
    PolicyDecisionAction,
    ProactiveOpportunity,
    TPOContext,
)


def _parse_hhmm(value: str, fallback: time) -> time:
    """HH:MM 문자열을 time으로 파싱하고 실패하면 안전한 기본값을 쓴다."""
    try:
        hour, minute = value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except (AttributeError, TypeError, ValueError):
        return fallback


def _in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """자정을 걸치는 quiet hours까지 포함해 현재 시각이 조용한 시간인지 판단한다."""
    start_t = _parse_hhmm(start, time(23, 0))
    end_t = _parse_hhmm(end, time(8, 0))
    current = now.time()
    if start_t == end_t:
        return False
    if start_t < end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t


def _quiet_hours_end(now: datetime, end: str) -> datetime:
    """defer_until에 사용할 다음 quiet-hours 종료 시각을 계산한다."""
    end_t = _parse_hhmm(end, time(8, 0))
    candidate = now.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _days_between(now: datetime, prior: datetime | None) -> int | None:
    """cooldown 비교를 위해 두 시각의 일수 차이를 반환한다."""
    if prior is None:
        return None
    return (now - prior).days


class TPOPolicyEngine:
    """proactive 후보를 사용자에게 말 걸어도 되는지 판정한다."""

    def evaluate(
        self, opportunity: ProactiveOpportunity, context: TPOContext
    ) -> PolicyDecision:
        """후보와 현재 TPO context를 평가해 send/defer/suppress 중 하나로 정한다."""
        reasons: list[str] = []
        urgent = self._is_urgent(opportunity)
        requested = opportunity.type == OpportunityType.REQUESTED_FOLLOWUP

        if not context.enabled or context.mode == "off":
            if not (urgent or requested):
                return PolicyDecision(
                    PolicyDecisionAction.SUPPRESS, ["proactive_disabled_or_off"]
                )
            reasons.append("mode_bypassed_for_urgent_or_requested_followup")

        if opportunity.confidence < context.min_confidence and not (urgent or requested):
            return PolicyDecision(
                PolicyDecisionAction.SUPPRESS,
                ["confidence_below_threshold"],
            )

        dismissed_age = _days_between(context.now, context.last_dismissed_at)
        if (
            dismissed_age is not None
            and dismissed_age < context.dismissed_cooldown_days
            and not requested
        ):
            return PolicyDecision(
                PolicyDecisionAction.SUPPRESS,
                ["dismissed_cooldown_active"],
            )

        sent_age = _days_between(context.now, context.last_sent_at)
        if (
            sent_age is not None
            and sent_age < context.topic_cooldown_days
            and not (urgent or requested)
        ):
            return PolicyDecision(
                PolicyDecisionAction.SUPPRESS,
                ["topic_cooldown_active"],
            )

        if (
            context.sent_today_count >= max(0, context.max_messages_per_day)
            and not (urgent or requested)
        ):
            return PolicyDecision(PolicyDecisionAction.DEFER, ["daily_budget_exhausted"])

        if _in_quiet_hours(
            context.now, context.quiet_hours_start, context.quiet_hours_end
        ) and not (urgent or requested):
            return PolicyDecision(
                PolicyDecisionAction.DEFER,
                ["quiet_hours_active"],
                defer_until=_quiet_hours_end(context.now, context.quiet_hours_end),
            )

        if opportunity.requires_user_approval:
            return PolicyDecision(
                PolicyDecisionAction.NEEDS_USER_APPROVAL,
                reasons + ["requires_user_approval"],
            )

        return PolicyDecision(PolicyDecisionAction.SEND_NOW, reasons or ["allowed"])

    def _is_urgent(self, opportunity: ProactiveOpportunity) -> bool:
        """quiet hours와 budget을 우회해도 되는 긴급 후보인지 판단한다."""
        return (
            opportunity.urgency >= 8
            or opportunity.priority >= 8
            or opportunity.type == OpportunityType.FAILURE_RECOVERY
        )
