"""Dreaming context cron용 Calendar/Gmail 런타임 provider resolver.

운영 환경의 Google Calendar/Gmail 연동은 사용자 홈의 live skill 디렉터리 아래
스크립트/venv에 있다. 이 모듈은 config 경로를 우선 읽고, 없을 때만 기존 live
기본 경로로 폴백한다. 스킬 경로 누락은 ``None`` provider로 반환해 collector가
``context_unavailable`` warning으로 degrade하도록 한다.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from simpleclaw.proactive.context_collectors import CalendarProvider, MailProvider

logger = logging.getLogger(__name__)

_DEFAULT_CALENDAR_SKILL_PATH = "~/.agents/skills/google-calendar-skill"
_DEFAULT_GMAIL_SKILL_PATH = "~/.agents/skills/gmail-skill"


def resolve_context_providers(
    config: dict[str, Any] | None,
) -> tuple[CalendarProvider | None, MailProvider | None]:
    """config/live skill 경로에서 Calendar/Gmail provider를 만든다."""
    cfg = config or {}
    calendar_path = Path(
        str(cfg.get("calendar_skill_path") or _DEFAULT_CALENDAR_SKILL_PATH)
    ).expanduser()
    gmail_path = Path(str(cfg.get("gmail_skill_path") or _DEFAULT_GMAIL_SKILL_PATH)).expanduser()

    calendar_provider: CalendarProvider | None = None
    if _is_skill_script_available(calendar_path, "gcal.py"):
        calendar_provider = SkillCalendarProvider(
            calendar_path,
            calendar_id=str(cfg.get("calendar_id", "primary")),
            limit=int(cfg.get("calendar_limit", 10)),
        )
    else:
        logger.info("Calendar context provider unavailable: skill path missing (%s)", calendar_path)

    mail_provider: MailProvider | None = None
    if _is_skill_script_available(gmail_path, "gmail.py"):
        mail_provider = SkillGmailProvider(gmail_path, limit=int(cfg.get("mail_limit", 10)))
    else:
        logger.info("Mail context provider unavailable: skill path missing (%s)", gmail_path)

    return calendar_provider, mail_provider


def _is_skill_script_available(skill_path: Path, script_name: str) -> bool:
    """스킬 스크립트와 실행 Python이 모두 있는지 확인한다."""
    return (skill_path / "scripts" / script_name).is_file() and _python_bin(skill_path).is_file()


def _python_bin(skill_path: Path) -> Path:
    """스킬 전용 venv Python 경로를 반환한다."""
    return skill_path / "scripts" / "venv" / "bin" / "python"


_CALENDAR_CODE = r"""
from __future__ import annotations
import json
import sys
from pathlib import Path

skill_path = Path(sys.argv[1])
calendar_id = sys.argv[2]
time_min = sys.argv[3]
time_max = sys.argv[4]
limit = int(sys.argv[5])
sys.path.insert(0, str(skill_path / "scripts"))
import gcal  # noqa: E402

service = gcal.get_calendar_service()
result = service.events().list(
    calendarId=calendar_id,
    timeMin=time_min,
    timeMax=time_max,
    maxResults=limit,
    singleEvents=True,
    orderBy="startTime",
).execute()
items = []
for event in result.get("items", []):
    start_obj = event.get("start") or {}
    end_obj = event.get("end") or {}
    attendees = []
    for attendee in event.get("attendees") or []:
        if isinstance(attendee, dict):
            value = attendee.get("email") or attendee.get("displayName") or ""
        else:
            value = str(attendee)
        if value:
            attendees.append(value)
    items.append({
        "id": event.get("id") or "",
        "title": event.get("summary") or event.get("title") or "",
        "start": start_obj.get("dateTime") or start_obj.get("date") or "",
        "end": end_obj.get("dateTime") or end_obj.get("date") or "",
        "location": event.get("location") or "",
        "attendees": attendees,
    })
print(json.dumps(items, ensure_ascii=False))
"""

_GMAIL_CODE = r"""
from __future__ import annotations
import json
import sys
from pathlib import Path

skill_path = Path(sys.argv[1])
query = sys.argv[2]
limit = int(sys.argv[3])
sys.path.insert(0, str(skill_path / "scripts"))
import gmail  # noqa: E402

service = gmail.get_gmail_service()
messages = service.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
items = []
for message in messages:
    msg_id = message.get("id")
    if not msg_id:
        continue
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="metadata",
        metadataHeaders=["Subject", "From", "Date"],
    ).execute()
    headers = {h.get("name", "").lower(): h.get("value", "") for h in msg.get("payload", {}).get("headers", [])}
    items.append({
        "id": msg_id,
        "subject": headers.get("subject", ""),
        "sender": headers.get("from", ""),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
    })
print(json.dumps(items, ensure_ascii=False))
"""


@dataclass(frozen=True)
class SkillCalendarProvider:
    """google-calendar-skill의 venv/script를 subprocess로 호출하는 provider."""

    skill_path: Path
    calendar_id: str = "primary"
    limit: int = 10

    def list_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Calendar API 결과를 collector가 이해하는 metadata dict로 정규화한다."""
        return _run_json_list(
            self.skill_path,
            [
                str(self.skill_path),
                self.calendar_id,
                start.isoformat(),
                end.isoformat(),
                str(max(1, int(self.limit))),
            ],
            _CALENDAR_CODE,
        )


@dataclass(frozen=True)
class SkillGmailProvider:
    """gmail-skill의 metadata search만 호출하고 body는 읽지 않는 provider."""

    skill_path: Path
    limit: int = 10

    def list_messages(self, since: datetime, query: str) -> list[dict[str, Any]]:
        """Gmail metadata headers/snippet/labels만 반환한다."""
        _ = since  # Gmail query가 window를 담는다. Protocol signature를 맞추기 위한 인자다.
        return _run_json_list(
            self.skill_path,
            [str(self.skill_path), query, str(max(1, int(self.limit)))],
            _GMAIL_CODE,
        )


def _run_json_list(skill_path: Path, args: list[str], code: str) -> list[dict[str, Any]]:
    """스킬 venv Python을 실행하고 JSON list stdout만 허용한다."""
    result = subprocess.run(
        [str(_python_bin(skill_path)), "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
        raise RuntimeError(message[:240])
    try:
        payload = json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid provider json: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("provider returned non-list json")
    return [item for item in payload if isinstance(item, dict)]
