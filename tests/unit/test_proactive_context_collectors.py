"""Dreaming context collector의 윈도우, 필터, redaction, fallback을 검증한다."""

from __future__ import annotations

from datetime import datetime, timedelta

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.proactive.context_collectors import (
    CalendarContextCollector,
    ConversationContextCollector,
    MailContextCollector,
)

NOW = datetime(2026, 6, 6, 9, 0, 0)


def test_conversation_collector_filters_24h_and_auto_channels_and_redacts(tmp_path):
    store = ConversationStore(tmp_path / "conversation.db")
    store.add_message(ConversationMessage(MessageRole.USER, "내 token=SECRET 회의 준비해줘", NOW - timedelta(hours=1), channel="telegram"))
    store.add_message(ConversationMessage(MessageRole.ASSISTANT, "좋아요", NOW - timedelta(minutes=50), channel="telegram"))
    store.add_message(ConversationMessage(MessageRole.USER, "cron noise", NOW - timedelta(minutes=10), channel="cron:daily"))
    store.add_message(ConversationMessage(MessageRole.USER, "old", NOW - timedelta(hours=25), channel="telegram"))

    snapshot = ConversationContextCollector(store, now=NOW).collect()

    assert len(snapshot.conversations) == 2
    assert snapshot.conversations[0].role == "user"
    assert "SECRET" not in snapshot.conversations[0].text
    assert "[REDACTED]" in snapshot.conversations[0].text
    assert all(item.channel != "cron:daily" for item in snapshot.conversations)


def test_calendar_collector_includes_next_24h_and_falls_back_on_provider_failure():
    class Provider:
        def list_events(self, start, end):
            return [
                {"id": "meet-1", "title": "Biz review", "start": (NOW + timedelta(hours=2)).isoformat(), "attendees": ["a@example.com"]},
                {"id": "later", "title": "Too late", "start": (NOW + timedelta(hours=30)).isoformat()},
            ]

    snapshot = CalendarContextCollector(Provider(), now=NOW).collect()

    assert [event.id for event in snapshot.calendar_events] == ["meet-1"]
    assert snapshot.warnings == []

    class Broken:
        def list_events(self, start, end):
            raise RuntimeError("auth failed")

    broken = CalendarContextCollector(Broken(), now=NOW).collect()
    assert broken.calendar_events == []
    assert broken.warnings[0]["kind"] == "context_unavailable"


def test_mail_collector_uses_metadata_only_filters_and_redacts():
    class Provider:
        def list_messages(self, since, query):
            return [
                {"id": "m1", "subject": "자료 확인", "sender": "boss@example.com", "received_at": (NOW - timedelta(hours=1)).isoformat(), "snippet": "api_key: SECRET please read", "labels": ["INBOX", "UNREAD", "CATEGORY_PRIMARY"]},
                {"id": "m2", "subject": "old", "received_at": (NOW - timedelta(hours=30)).isoformat(), "labels": ["INBOX"]},
            ]

    snapshot = MailContextCollector(Provider(), now=NOW).collect()

    assert [mail.id for mail in snapshot.mail_messages] == ["m1"]
    assert snapshot.mail_messages[0].body == ""
    assert "SECRET" not in snapshot.mail_messages[0].snippet
    assert "[REDACTED]" in snapshot.mail_messages[0].snippet
