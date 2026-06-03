"""proactive Telegram presenter와 callback 상태 전이를 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from simpleclaw.proactive.models import (
    OpportunityStatus,
    OpportunityType,
    PolicyDecision,
    PolicyDecisionAction,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)
from simpleclaw.proactive.presenter import (
    ProactivePresenter,
    build_proactive_callback_data,
)
from simpleclaw.proactive.store import OpportunityStore


class StaticPolicy:
    def __init__(self, action: PolicyDecisionAction) -> None:
        self.action = action
        self.contexts = []

    def evaluate(self, opportunity, context):
        self.contexts.append(context)
        return PolicyDecision(self.action, ["test"])


class FakeTelegramBot:
    def __init__(self) -> None:
        self.sent = []

    async def send_proactive_opportunity(self, *, chat_id, opportunity, text=None):
        self.sent.append((chat_id, opportunity, text))


def _opportunity(**kwargs) -> ProactiveOpportunity:
    data = {
        "id": "abc123",
        "type": OpportunityType.REPEATED_REQUEST,
        "title": "아침 리포트 등록",
        "message_draft": "매일 아침 리포트를 자동으로 받아볼까요?",
        "evidence": ["평일 9시 요청 5회", "최근 2주 반복"],
        "confidence": 0.91,
        "cooldown_key": "morning-report",
        "requires_user_approval": True,
        "suggested_action": SuggestedAction(
            kind=SuggestedActionKind.CREATE_CRON,
            label="매일 9시 리포트",
            payload={
                "cron_expression": "0 9 * * 1-5",
                "action_type": "prompt",
                "action_reference": "아침 리포트를 요약해줘",
                "name": "proactive-morning-report",
            },
        ),
    }
    data.update(kwargs)
    return ProactiveOpportunity(**data)


@pytest.mark.asyncio
async def test_tick_sends_only_policy_allowed_pending_opportunities(tmp_path):
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    allowed = _opportunity(id="allowed", cooldown_key="allowed")
    blocked = _opportunity(id="blocked", cooldown_key="blocked")
    store.save_all([allowed, blocked])

    bot = FakeTelegramBot()

    class PerItemPolicy:
        def evaluate(self, opportunity, context):
            if opportunity.id == "allowed":
                return PolicyDecision(PolicyDecisionAction.NEEDS_USER_APPROVAL, ["ok"])
            return PolicyDecision(PolicyDecisionAction.SUPPRESS, ["no"])

    presenter = ProactivePresenter(
        store=store,
        policy=PerItemPolicy(),
        telegram_bot=bot,
        chat_id=123,
        config={"enabled": True, "mode": "low", "max_messages_per_day": 10},
    )

    sent_count = await presenter.tick(now=datetime(2026, 6, 3, 10, 0))

    assert sent_count == 1
    assert [entry[1].id for entry in bot.sent] == ["allowed"]
    reloaded = {item.id: item for item in store.list_all()}
    assert reloaded["allowed"].status == OpportunityStatus.SENT
    assert reloaded["allowed"].last_presented_at is not None
    assert reloaded["allowed"].presented_count == 1
    assert reloaded["blocked"].status == OpportunityStatus.PENDING


@pytest.mark.asyncio
async def test_presented_message_contains_evidence_action_and_choices(tmp_path):
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    store.save_all([_opportunity()])
    bot = FakeTelegramBot()
    presenter = ProactivePresenter(
        store=store,
        policy=StaticPolicy(PolicyDecisionAction.SEND_NOW),
        telegram_bot=bot,
        chat_id=456,
        config={"enabled": True, "mode": "low"},
    )

    await presenter.tick(now=datetime(2026, 6, 3, 10, 0))

    message = bot.sent[0][2]
    assert "평일 9시 요청 5회" in message
    assert "매일 9시 리포트" in message
    assert "등록" in message
    assert "시간 변경" in message
    assert "나중에" in message
    assert "아니요" in message


@pytest.mark.asyncio
async def test_dismiss_and_snooze_callbacks_update_store_state(tmp_path):
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    store.save_all([_opportunity(id="dismiss-me"), _opportunity(id="snooze-me")])
    presenter = ProactivePresenter(
        store=store,
        policy=StaticPolicy(PolicyDecisionAction.SEND_NOW),
        telegram_bot=FakeTelegramBot(),
        chat_id=456,
        config={"enabled": True, "mode": "low"},
    )

    dismissed = await presenter.handle_callback("dismiss", "dismiss-me")
    snoozed = await presenter.handle_callback("snooze", "snooze-me")

    assert "거절" in dismissed
    assert "나중" in snoozed
    reloaded = {item.id: item for item in store.list_all()}
    assert reloaded["dismiss-me"].status == OpportunityStatus.DISMISSED
    assert reloaded["snooze-me"].status == OpportunityStatus.SNOOZED


def test_callback_data_stays_within_telegram_64_byte_limit():
    callback_data = build_proactive_callback_data("edit", "f" * 32)

    assert len(callback_data.encode("utf-8")) <= 64
    assert callback_data == "pc:edit:" + "f" * 32


@pytest.mark.asyncio
async def test_cooldown_and_daily_budget_are_passed_to_policy(tmp_path):
    now = datetime(2026, 6, 3, 10, 0)
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    prior_sent = _opportunity(
        id="sent",
        cooldown_key="topic",
        status=OpportunityStatus.SENT,
        last_presented_at=now - timedelta(hours=2),
    )
    prior_dismissed = _opportunity(
        id="dismissed",
        cooldown_key="topic",
        status=OpportunityStatus.DISMISSED,
        updated_at=now - timedelta(days=1),
    )
    pending = _opportunity(id="pending", cooldown_key="topic")
    store.save_all([prior_sent, prior_dismissed, pending])
    policy = StaticPolicy(PolicyDecisionAction.SUPPRESS)
    presenter = ProactivePresenter(
        store=store,
        policy=policy,
        telegram_bot=FakeTelegramBot(),
        chat_id=456,
        config={
            "enabled": True,
            "mode": "low",
            "quiet_hours": {"start": "22:00", "end": "07:00"},
            "max_messages_per_day": 3,
            "topic_cooldown_days": 14,
            "dismissed_cooldown_days": 30,
            "min_confidence": 0.75,
        },
    )

    await presenter.tick(now=now)

    context = policy.contexts[0]
    assert context.sent_today_count == 1
    assert context.last_sent_at == prior_sent.last_presented_at
    assert context.last_dismissed_at == prior_dismissed.updated_at
    assert context.quiet_hours_start == "22:00"
    assert context.max_messages_per_day == 3
