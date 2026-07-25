"""Context snapshot 기반 cron 후보 planner를 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta

from simpleclaw.proactive.context_collectors import (
    CalendarEventContext,
    ConversationContextItem,
    DreamingContextSnapshot,
    MailContext,
)
from simpleclaw.proactive.context_planner import ContextCronPlanner
from simpleclaw.proactive.models import OpportunityType, SuggestedActionKind

NOW = datetime(2026, 6, 6, 9, 0, 0)


def test_planner_creates_approval_required_one_shot_reminder_without_raw_mail_body_or_secret():
    snapshot = DreamingContextSnapshot(
        conversations=[ConversationContextItem(1, "user", "내일 Biz review 자료 준비해야 해", NOW - timedelta(hours=1), "telegram")],
        calendar_events=[CalendarEventContext("event-1", "Biz review", NOW + timedelta(hours=3))],
        mail_messages=[MailContext("mail-1", "Biz review deck", "boss@example.com", NOW - timedelta(hours=2), "token=[REDACTED] deck attached", body="RAW BODY SECRET")],
        source_context_window={"conversation_lookback_hours": 24, "calendar_lookahead_hours": 24, "mail_lookback_hours": 24},
    )

    opportunities = ContextCronPlanner(now=NOW).plan(snapshot)

    one_shot = next(op for op in opportunities if op.type == OpportunityType.CONTEXTUAL_REMINDER)
    assert one_shot.requires_user_approval is True
    assert one_shot.suggested_action.kind == SuggestedActionKind.CREATE_CRON
    payload = one_shot.suggested_action.payload
    assert payload["run_once"] is True
    assert payload["expires_at"]
    assert payload["privacy_level"] == "metadata_only"
    assert payload["name"].startswith("context-reminder-event-1")
    assert payload["cron_expression"]
    assert payload["action_type"] == "prompt"
    assert "최근 대화/일정/메일 context를 다시 조회" in payload["action_reference"]
    combined = "\n".join(one_shot.evidence + [payload["action_reference"]])
    assert "RAW BODY" not in combined
    assert "SECRET" not in combined
    assert "token=[REDACTED]" in combined


def test_planner_creates_recurring_briefing_for_explicit_repeated_intent_and_deterministic_key():
    snapshot = DreamingContextSnapshot(
        conversations=[ConversationContextItem(1, "user", "매일 아침 메일 일정 브리핑 정기 알림 해줘", NOW - timedelta(hours=1), "telegram")],
    )

    planner = ContextCronPlanner(now=NOW, allow_recurring=True)
    first = planner.plan(snapshot)
    second = planner.plan(snapshot)

    recurring = next(op for op in first if op.type == OpportunityType.INTEREST_BRIEFING)
    assert recurring.requires_user_approval is True
    assert recurring.suggested_action.payload["run_once"] is False
    assert recurring.suggested_action.payload["cron_expression"] == "0 9 * * *"
    assert first[0].cooldown_key == second[0].cooldown_key
