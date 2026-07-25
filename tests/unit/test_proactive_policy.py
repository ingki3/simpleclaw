"""Proactive TPO 정책 엔진 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

from simpleclaw.config import load_daemon_config
from simpleclaw.proactive import (
    OpportunityType,
    PolicyDecisionAction,
    ProactiveOpportunity,
    TPOContext,
    TPOPolicyEngine,
)


def _context(**overrides: object) -> TPOContext:
    """기본적으로 발송 가능한 TPO context를 만든 뒤 필요한 값만 덮어쓴다."""
    data = {
        "enabled": True,
        "mode": "normal",
        "now": datetime(2026, 6, 3, 10, 0),
        "sent_today_count": 0,
    }
    data.update(overrides)
    return TPOContext(**data)


def _opportunity(**overrides: object) -> ProactiveOpportunity:
    """정책 테스트용 고신뢰 후보를 만든다."""
    data = {
        "type": OpportunityType.REPEATED_REQUEST,
        "title": "반복 요청",
        "message_draft": "cron 등록을 제안합니다.",
        "confidence": 0.9,
        "cooldown_key": "cron:daily",
    }
    data.update(overrides)
    return ProactiveOpportunity(**data)


def test_quiet_hours_defers_low_or_normal_priority() -> None:
    """quiet hours에는 일반 후보가 즉시 발송되지 않고 defer된다."""
    decision = TPOPolicyEngine().evaluate(
        _opportunity(), _context(now=datetime(2026, 6, 3, 23, 30))
    )

    assert decision.action == PolicyDecisionAction.DEFER
    assert "quiet_hours_active" in decision.reasons
    assert decision.defer_until == datetime(2026, 6, 4, 8, 0)


def test_urgent_failure_and_requested_followup_bypass_quiet_hours() -> None:
    """긴급 장애/명시 요청 follow-up은 quiet hours 예외로 발송 가능하다."""
    engine = TPOPolicyEngine()
    quiet_context = _context(now=datetime(2026, 6, 3, 23, 30))

    urgent = engine.evaluate(
        _opportunity(type=OpportunityType.FAILURE_RECOVERY, urgency=9), quiet_context
    )
    requested = engine.evaluate(
        _opportunity(type=OpportunityType.REQUESTED_FOLLOWUP, confidence=0.2),
        quiet_context,
    )

    assert urgent.action == PolicyDecisionAction.SEND_NOW
    assert requested.action == PolicyDecisionAction.SEND_NOW


def test_daily_budget_exhausted_defers() -> None:
    """하루 발송 예산을 넘으면 일반 후보는 보류된다."""
    decision = TPOPolicyEngine().evaluate(
        _opportunity(), _context(sent_today_count=1, max_messages_per_day=1)
    )

    assert decision.action == PolicyDecisionAction.DEFER
    assert "daily_budget_exhausted" in decision.reasons


def test_recent_sent_or_dismissed_suppresses_same_cooldown_key() -> None:
    """최근 발송/거절된 topic은 cooldown 기간 동안 억제된다."""
    engine = TPOPolicyEngine()
    now = datetime(2026, 6, 3, 10, 0)

    sent = engine.evaluate(
        _opportunity(),
        _context(now=now, last_sent_at=now - timedelta(days=3)),
    )
    dismissed = engine.evaluate(
        _opportunity(),
        _context(now=now, last_dismissed_at=now - timedelta(days=3)),
    )

    assert sent.action == PolicyDecisionAction.SUPPRESS
    assert "topic_cooldown_active" in sent.reasons
    assert dismissed.action == PolicyDecisionAction.SUPPRESS
    assert "dismissed_cooldown_active" in dismissed.reasons


def test_low_confidence_suppresses() -> None:
    """신뢰도 기준 미달 후보는 사용자에게 말을 걸지 않는다."""
    decision = TPOPolicyEngine().evaluate(
        _opportunity(confidence=0.2), _context(min_confidence=0.75)
    )

    assert decision.action == PolicyDecisionAction.SUPPRESS
    assert "confidence_below_threshold" in decision.reasons


def test_mode_off_suppresses_except_requested_or_urgent() -> None:
    """mode=off에서는 명시 follow-up/긴급 후보 외에는 fail-closed로 억제한다."""
    engine = TPOPolicyEngine()

    normal = engine.evaluate(_opportunity(), _context(mode="off"))
    requested = engine.evaluate(
        _opportunity(type=OpportunityType.REQUESTED_FOLLOWUP), _context(mode="off")
    )

    assert normal.action == PolicyDecisionAction.SUPPRESS
    assert requested.action == PolicyDecisionAction.SEND_NOW


def test_requires_user_approval_returns_structured_action() -> None:
    """승인이 필요한 후보는 발송 대신 needs_user_approval로 구조화해 반환한다."""
    decision = TPOPolicyEngine().evaluate(
        _opportunity(requires_user_approval=True), _context()
    )

    assert decision.action == PolicyDecisionAction.NEEDS_USER_APPROVAL
    assert decision.to_dict()["action"] == "needs_user_approval"


def test_daemon_proactive_defaults_are_low_noise() -> None:
    """config 기본값은 proactive 발송을 꺼둔 low-noise 정책으로 로드된다."""
    cfg = load_daemon_config("/tmp/definitely-missing-simpleclaw-config.yaml")

    assert cfg["proactive"]["enabled"] is False
    assert cfg["proactive"]["mode"] == "low"
    assert cfg["proactive"]["max_messages_per_day"] == 1
    assert cfg["proactive"]["store_file"].endswith("proactive_opportunities.jsonl")
