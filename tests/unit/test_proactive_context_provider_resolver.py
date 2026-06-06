"""Dreaming context cron 런타임 provider resolver를 검증한다."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from simpleclaw.proactive.context_provider_resolver import resolve_context_providers

NOW = datetime(2026, 6, 6, 9, 0, 0)


def _make_skill_python(skill_dir: Path) -> None:
    bin_dir = skill_dir / "scripts" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    os.symlink(sys.executable, bin_dir / "python")


def test_resolver_uses_calendar_skill_path_to_collect_next_24h_metadata(tmp_path):
    skill_dir = tmp_path / "google-calendar-skill"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    _make_skill_python(skill_dir)
    (scripts_dir / "gcal.py").write_text(
        """
from __future__ import annotations

class _Execute:
    def execute(self):
        return {"items": [
            {"id": "evt-1", "summary": "Review", "start": {"dateTime": "2026-06-06T11:00:00"}, "end": {"dateTime": "2026-06-06T12:00:00"}, "location": "HQ", "attendees": [{"email": "a@example.com"}]},
        ]}

class _Events:
    def list(self, **kwargs):
        assert kwargs["calendarId"] == "primary"
        assert kwargs["singleEvents"] is True
        assert kwargs["orderBy"] == "startTime"
        return _Execute()

class _Service:
    def events(self):
        return _Events()

def get_calendar_service():
    return _Service()
""",
        encoding="utf-8",
    )

    calendar_provider, mail_provider = resolve_context_providers(
        {"calendar_skill_path": str(skill_dir), "gmail_skill_path": str(tmp_path / "missing")}
    )

    assert calendar_provider is not None
    assert mail_provider is None
    events = calendar_provider.list_events(NOW, NOW + timedelta(hours=24))
    assert events == [
        {
            "id": "evt-1",
            "title": "Review",
            "start": "2026-06-06T11:00:00",
            "end": "2026-06-06T12:00:00",
            "location": "HQ",
            "attendees": ["a@example.com"],
        }
    ]


def test_resolver_uses_gmail_skill_path_to_collect_metadata_without_body(tmp_path):
    skill_dir = tmp_path / "gmail-skill"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    _make_skill_python(skill_dir)
    (scripts_dir / "gmail.py").write_text(
        """
from __future__ import annotations

class _ListExecute:
    def execute(self):
        return {"messages": [{"id": "m1"}]}

class _GetExecute:
    def execute(self):
        return {
            "id": "m1",
            "snippet": "short metadata only",
            "labelIds": ["INBOX"],
            "payload": {"headers": [
                {"name": "Subject", "value": "Deck"},
                {"name": "From", "value": "boss@example.com"},
                {"name": "Date", "value": "Sat, 06 Jun 2026 08:30:00 +0900"},
            ]},
        }

class _Messages:
    def list(self, **kwargs):
        assert kwargs["userId"] == "me"
        assert kwargs["q"] == "newer_than:1d"
        return _ListExecute()

    def get(self, **kwargs):
        assert kwargs["format"] == "metadata"
        assert "Body" not in kwargs.get("metadataHeaders", [])
        return _GetExecute()

class _Users:
    def messages(self):
        return _Messages()

class _Service:
    def users(self):
        return _Users()

def get_gmail_service():
    return _Service()
""",
        encoding="utf-8",
    )

    calendar_provider, mail_provider = resolve_context_providers(
        {"calendar_skill_path": str(tmp_path / "missing"), "gmail_skill_path": str(skill_dir)}
    )

    assert calendar_provider is None
    assert mail_provider is not None
    messages = mail_provider.list_messages(NOW - timedelta(hours=24), "newer_than:1d")
    assert messages == [
        {
            "id": "m1",
            "subject": "Deck",
            "sender": "boss@example.com",
            "date": "Sat, 06 Jun 2026 08:30:00 +0900",
            "snippet": "short metadata only",
            "labels": ["INBOX"],
        }
    ]
    assert "body" not in messages[0]
