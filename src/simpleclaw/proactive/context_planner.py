"""Dreaming context snapshot을 사용자 승인형 cron 후보로 변환하는 planner.

Planner는 cron 생성 후보만 만들며 side effect를 만들지 않는다. action_reference에는
런타임이 다시 context를 조회하라는 최소 지시만 넣고, 수집한 메일 본문/secret raw text는
payload나 evidence에 저장하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from simpleclaw.proactive.context_collectors import (
    DreamingContextSnapshot,
    redact_context_text,
)
from simpleclaw.proactive.models import (
    OpportunityType,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)

_RECURRING_RE = re.compile(r"(매일|매주|정기|반복|daily|weekly|recurring|briefing|브리핑)", re.IGNORECASE)


@dataclass
class ContextCronPlanner:
    """대화·일정·메일 snapshot에서 one-shot/recurring cron 후보를 만든다."""

    now: datetime | None = None
    allow_one_shot: bool = True
    allow_recurring: bool = True
    max_opportunities: int = 3

    def plan(self, snapshot: DreamingContextSnapshot) -> list[ProactiveOpportunity]:
        """Snapshot을 approval-required ProactiveOpportunity 목록으로 변환한다."""
        now = self.now or datetime.now()
        opportunities: list[ProactiveOpportunity] = []
        if self.allow_one_shot:
            opportunities.extend(self._plan_one_shot(snapshot, now))
        if self.allow_recurring:
            recurring = self._plan_recurring(snapshot, now)
            if recurring is not None:
                opportunities.append(recurring)
        return opportunities[: max(1, int(self.max_opportunities))]

    def _plan_one_shot(self, snapshot: DreamingContextSnapshot, now: datetime) -> list[ProactiveOpportunity]:
        """다가오는 일정과 최근 대화/메일이 있으면 회의 전 준비 알림 후보를 만든다."""
        if not snapshot.calendar_events:
            return []
        related = bool(snapshot.conversations or snapshot.mail_messages)
        if not related:
            return []
        opportunities: list[ProactiveOpportunity] = []
        for event in snapshot.calendar_events:
            run_at = max(now + timedelta(minutes=5), event.start - timedelta(hours=1))
            expires_at = event.start + timedelta(hours=2)
            fingerprint = self._fingerprint("one-shot", event.id, event.start.isoformat(), *[m.id for m in snapshot.mail_messages[:3]])
            name = f"context-reminder-{self._safe_slug(event.id or event.title)}-{fingerprint[:8]}"
            payload = {
                "name": name,
                "cron_expression": f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *",
                "action_type": "prompt",
                "action_reference": (
                    "최근 대화/일정/메일 context를 다시 조회해 이 일정 전 필요한 알림/브리핑을 생성하라. "
                    f"event_fingerprint={fingerprint[:12]}"
                ),
                "run_once": True,
                "expires_at": expires_at.isoformat(),
                "max_runs": 1,
                "source_context_window": dict(snapshot.source_context_window),
                "privacy_level": "metadata_only",
            }
            evidence = [
                f"calendar_event_id={redact_context_text(event.id, limit=80)}",
                f"calendar_title={redact_context_text(event.title, limit=120)}",
                f"calendar_start={event.start.isoformat()}",
            ]
            if snapshot.mail_messages:
                mail = snapshot.mail_messages[0]
                evidence.append(f"mail_subject={mail.subject}")
                if mail.snippet:
                    evidence.append(f"mail_snippet={mail.snippet}")
            if snapshot.conversations:
                evidence.append(f"conversation_msg_id={snapshot.conversations[-1].id}")
            opportunities.append(
                ProactiveOpportunity(
                    type=OpportunityType.CONTEXTUAL_REMINDER,
                    title=f"일정 준비 알림 후보: {event.title}",
                    message_draft=f"'{event.title}' 일정 전에 관련 대화/메일을 다시 확인해 알림을 드릴까요?",
                    evidence=evidence,
                    confidence=0.82,
                    priority=3,
                    urgency=2,
                    cooldown_key=f"context-reminder:{fingerprint}",
                    suggested_action=SuggestedAction(SuggestedActionKind.CREATE_CRON, "등록", payload),
                    requires_user_approval=True,
                    expires_at=expires_at,
                    source="dreaming_context_planner",
                )
            )
        return opportunities

    def _plan_recurring(self, snapshot: DreamingContextSnapshot, now: datetime) -> ProactiveOpportunity | None:
        """명시적 반복 브리핑 의도가 보이면 recurring cron 후보를 만든다."""
        text = "\n".join(item.text for item in snapshot.conversations if item.role == "user")
        if not _RECURRING_RE.search(text):
            return None
        fingerprint = self._fingerprint("recurring", text[:200])
        payload = {
            "name": f"context-daily-briefing-{fingerprint[:8]}",
            "cron_expression": "0 9 * * *",
            "action_type": "prompt",
            "action_reference": "최근 대화/일정/메일 context를 다시 조회해 오늘 필요한 알림/브리핑을 생성하라.",
            "run_once": False,
            "source_context_window": dict(snapshot.source_context_window),
            "privacy_level": "metadata_only",
        }
        return ProactiveOpportunity(
            type=OpportunityType.INTEREST_BRIEFING,
            title="정기 context 브리핑 cron 후보",
            message_draft="대화에서 정기 브리핑 의도가 보여요. 매일 아침 context 브리핑 cron으로 등록할까요?",
            evidence=[f"recurring_intent={redact_context_text(text, limit=160)}"],
            confidence=0.8,
            priority=2,
            urgency=0,
            cooldown_key=f"context-briefing:{fingerprint}",
            suggested_action=SuggestedAction(SuggestedActionKind.CREATE_CRON, "등록", payload),
            requires_user_approval=True,
            expires_at=now + timedelta(days=14),
            source="dreaming_context_planner",
        )

    @staticmethod
    def _fingerprint(*parts: str) -> str:
        """중복/cooldown key에 사용할 안정적인 fingerprint를 만든다."""
        h = hashlib.sha256()
        for part in parts:
            h.update(str(part).encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    @staticmethod
    def _safe_slug(value: str) -> str:
        """Cron job 이름에 쓰기 안전한 slug로 축약한다."""
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip()).strip("-").lower()
        return (slug or "event")[:40]
