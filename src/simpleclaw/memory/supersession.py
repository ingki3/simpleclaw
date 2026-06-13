"""Supersession and stale-event guards for long-term memory.

These helpers keep one-off event memories (forums, matches, schedules) from
remaining active after their explicit date has passed, and let operator/user
corrections outrank older inferred memories.
"""

from __future__ import annotations

import re
from datetime import datetime

from simpleclaw.memory.models import MemoryItem, MemoryItemType


_EVENT_KEYWORDS = (
    "event",
    "forum",
    "conference",
    "meetup",
    "schedule",
    "일정",
    "행사",
    "포럼",
    "경기",
    "세미나",
    "컨퍼런스",
    "공지",
    "모니터링",
)
_ISO_DATE_RE = re.compile(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b")
_KOREAN_DATE_RE = re.compile(r"\b(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일")
_MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일")


def _candidate_dates(text: str, *, now: datetime) -> list[datetime]:
    dates: list[datetime] = []
    for pattern in (_ISO_DATE_RE, _KOREAN_DATE_RE):
        for year_s, month_s, day_s in pattern.findall(text):
            try:
                dates.append(datetime(int(year_s), int(month_s), int(day_s)))
            except ValueError:
                continue
    for month_s, day_s in _MONTH_DAY_RE.findall(text):
        try:
            dates.append(datetime(now.year, int(month_s), int(day_s)))
        except ValueError:
            continue
    return dates


def is_expired_event_memory(text: str, *, now: datetime | None = None) -> bool:
    """Return True when text describes a one-off dated event whose date is past.

    The guard intentionally requires both an event-like keyword and a concrete
    date. This avoids expiring durable preferences just because they mention an
    old date, while catching cases such as "2026-05-29 KSAI 포럼 모니터링".
    """
    if not text.strip():
        return False
    ts = now or datetime.now()
    lowered = text.lower()
    if not any(keyword in lowered for keyword in _EVENT_KEYWORDS):
        return False
    dates = _candidate_dates(text, now=ts)
    if not dates:
        return False
    today = datetime(ts.year, ts.month, ts.day)
    return all(date < today for date in dates)


def is_manual_correction_memory_item(item: MemoryItem) -> bool:
    """Return True for durable corrections/decisions that should outrank raw memory."""
    metadata = item.metadata or {}
    if metadata.get("manual_correction") or metadata.get("supersedes"):
        return True
    if item.type is MemoryItemType.DECISION:
        text = item.text.strip().lower()
        return text.startswith(("결정:", "decision:")) or any(
            marker in text for marker in ("더 이상", "종료", "아닙니다", "not ")
        )
    return False


def memory_item_supersession_boost(item: MemoryItem) -> float:
    """Extra ranking weight for corrections so they beat stale raw memories."""
    return 0.25 if is_manual_correction_memory_item(item) else 0.0


__all__ = [
    "is_expired_event_memory",
    "is_manual_correction_memory_item",
    "memory_item_supersession_boost",
]