"""Dreaming context-aware cron 후보 생성을 위한 안전한 context collector.

대화·일정·메일을 하나의 스냅샷으로 모으되, opportunity evidence/action_reference에
장기 보관될 수 있는 값은 metadata 중심으로 제한하고 흔한 secret 패턴은 즉시 가린다.
외부 provider 장애는 Dreaming 전체 실패가 아니라 warning evidence로 축약된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import MessageRole, is_auto_trigger_channel

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\b\s*([:=])\s*([^\s,;]+)"
)


def redact_context_text(text: str, *, limit: int = 240) -> str:
    """장기 evidence에 들어갈 수 있는 context 문자열에서 secret-like 값을 제거한다."""
    redacted = _SECRET_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)} [REDACTED]" if m.group(2) == ":" else f"{m.group(1)}{m.group(2)}[REDACTED]",
        str(text or ""),
    )
    return redacted.strip()[:limit]


@dataclass(frozen=True)
class ConversationContextItem:
    """최근 대화 한 줄. 본문은 redaction/truncation이 적용된 요약 입력이다."""

    id: int
    role: str
    text: str
    timestamp: datetime
    channel: str | None = None


@dataclass(frozen=True)
class CalendarEventContext:
    """Calendar provider에서 읽은 다음 24시간 일정 metadata."""

    id: str
    title: str
    start: datetime
    end: datetime | None = None
    location: str = ""
    attendees: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MailContext:
    """Gmail provider에서 읽은 최근 메일 metadata. body 전문은 저장하지 않는다."""

    id: str
    subject: str
    sender: str = ""
    received_at: datetime | None = None
    snippet: str = ""
    labels: list[str] = field(default_factory=list)
    body: str = ""


@dataclass(frozen=True)
class DreamingContextSnapshot:
    """Context planner가 소비하는 안전 스냅샷."""

    conversations: list[ConversationContextItem] = field(default_factory=list)
    calendar_events: list[CalendarEventContext] = field(default_factory=list)
    mail_messages: list[MailContext] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    source_context_window: dict[str, str | int] = field(default_factory=dict)


class CalendarProvider(Protocol):
    """테스트/런타임 Calendar provider의 최소 인터페이스."""

    def list_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]: ...


class MailProvider(Protocol):
    """테스트/런타임 Gmail provider의 최소 인터페이스."""

    def list_messages(self, since: datetime, query: str) -> list[dict[str, Any]]: ...


class ConversationContextCollector:
    """ConversationStore에서 최근 organic user/assistant 대화만 수집한다."""

    def __init__(self, store: ConversationStore, *, lookback_hours: int = 24, now: datetime | None = None) -> None:
        self._store = store
        self._lookback_hours = max(1, int(lookback_hours))
        self._now = now

    def collect(self) -> DreamingContextSnapshot:
        """최근 window 내 대화를 redaction하여 snapshot으로 반환한다."""
        now = self._now or datetime.now()
        since = now - timedelta(hours=self._lookback_hours)
        items: list[ConversationContextItem] = []
        for msg_id, msg in self._store.get_since_with_ids(since):
            if msg.timestamp < since or msg.timestamp > now:
                continue
            if msg.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
                continue
            if is_auto_trigger_channel(msg.channel):
                continue
            items.append(
                ConversationContextItem(
                    id=msg_id,
                    role=msg.role.value,
                    text=redact_context_text(msg.content),
                    timestamp=msg.timestamp,
                    channel=msg.channel,
                )
            )
        return DreamingContextSnapshot(
            conversations=items,
            source_context_window={"conversation_lookback_hours": self._lookback_hours},
        )


class CalendarContextCollector:
    """Calendar provider에서 다음 24시간 일정 metadata만 읽는다."""

    def __init__(self, provider: CalendarProvider | None, *, lookahead_hours: int = 24, now: datetime | None = None) -> None:
        self._provider = provider
        self._lookahead_hours = max(1, int(lookahead_hours))
        self._now = now

    def collect(self) -> DreamingContextSnapshot:
        """Provider 장애를 warning으로 흡수하면서 일정 metadata를 반환한다."""
        now = self._now or datetime.now()
        end = now + timedelta(hours=self._lookahead_hours)
        if self._provider is None:
            return DreamingContextSnapshot(warnings=[{"kind": "context_unavailable", "source": "calendar", "reason": "provider_unconfigured"}])
        try:
            raw_events = self._provider.list_events(now, end)
        except Exception as exc:  # noqa: BLE001 — provider 장애는 dreaming 전체를 막지 않는다.
            return DreamingContextSnapshot(warnings=[{"kind": "context_unavailable", "source": "calendar", "reason": redact_context_text(str(exc), limit=120)}])
        events: list[CalendarEventContext] = []
        for raw in raw_events or []:
            start = _coerce_datetime(raw.get("start"))
            if start is None or start < now or start > end:
                continue
            attendees = raw.get("attendees") or []
            if isinstance(attendees, str):
                attendees = [attendees]
            events.append(
                CalendarEventContext(
                    id=str(raw.get("id") or raw.get("event_id") or raw.get("title") or start.isoformat()),
                    title=redact_context_text(str(raw.get("title") or raw.get("summary") or "(제목 없음)")),
                    start=start,
                    end=_coerce_datetime(raw.get("end")),
                    location=redact_context_text(str(raw.get("location") or ""), limit=120),
                    attendees=[redact_context_text(str(a), limit=120) for a in attendees[:5]],
                )
            )
        return DreamingContextSnapshot(calendar_events=events, source_context_window={"calendar_lookahead_hours": self._lookahead_hours})


class MailContextCollector:
    """Gmail provider에서 최근 metadata 중심 메일만 읽고 본문 전문은 버린다."""

    def __init__(self, provider: MailProvider | None, *, lookback_hours: int = 24, query: str = "in:inbox newer_than:1d", now: datetime | None = None) -> None:
        self._provider = provider
        self._lookback_hours = max(1, int(lookback_hours))
        self._query = query
        self._now = now

    def collect(self) -> DreamingContextSnapshot:
        """Provider 장애를 warning으로 흡수하면서 최근 메일 metadata를 반환한다."""
        now = self._now or datetime.now()
        since = now - timedelta(hours=self._lookback_hours)
        if self._provider is None:
            return DreamingContextSnapshot(warnings=[{"kind": "context_unavailable", "source": "mail", "reason": "provider_unconfigured"}])
        try:
            raw_messages = self._provider.list_messages(since, self._query)
        except Exception as exc:  # noqa: BLE001
            return DreamingContextSnapshot(warnings=[{"kind": "context_unavailable", "source": "mail", "reason": redact_context_text(str(exc), limit=120)}])
        messages: list[MailContext] = []
        for raw in raw_messages or []:
            received_at = _coerce_datetime(raw.get("received_at") or raw.get("date"))
            if received_at is not None and (received_at < since or received_at > now):
                continue
            labels = [str(label) for label in (raw.get("labels") or raw.get("label_ids") or [])]
            messages.append(
                MailContext(
                    id=str(raw.get("id") or raw.get("message_id") or raw.get("subject") or "mail"),
                    subject=redact_context_text(str(raw.get("subject") or "(제목 없음)")),
                    sender=redact_context_text(str(raw.get("sender") or raw.get("from") or ""), limit=120),
                    received_at=received_at,
                    snippet=redact_context_text(str(raw.get("snippet") or raw.get("summary") or "")),
                    labels=labels,
                    body="",
                )
            )
        return DreamingContextSnapshot(mail_messages=messages, source_context_window={"mail_lookback_hours": self._lookback_hours, "mail_query": self._query})


def merge_snapshots(*snapshots: DreamingContextSnapshot) -> DreamingContextSnapshot:
    """여러 collector 결과를 하나의 snapshot으로 병합한다."""
    conversations: list[ConversationContextItem] = []
    events: list[CalendarEventContext] = []
    mails: list[MailContext] = []
    warnings: list[dict[str, str]] = []
    window: dict[str, str | int] = {}
    for snapshot in snapshots:
        conversations.extend(snapshot.conversations)
        events.extend(snapshot.calendar_events)
        mails.extend(snapshot.mail_messages)
        warnings.extend(snapshot.warnings)
        window.update(snapshot.source_context_window)
    return DreamingContextSnapshot(conversations=conversations, calendar_events=events, mail_messages=mails, warnings=warnings, source_context_window=window)


def _coerce_datetime(value: object) -> datetime | None:
    """Provider가 주는 ISO 문자열/datetime을 datetime으로 정규화한다."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).replace(tzinfo=None)
        except ValueError:
            return None
    return None
