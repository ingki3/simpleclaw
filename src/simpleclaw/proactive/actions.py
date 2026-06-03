"""승인된 proactive suggested action 실행기.

사용자 승인 전에는 어떠한 외부 side effect도 만들지 않는다. Telegram callback에서
accept/edit/dismiss/snooze가 들어온 뒤에만 이 실행기가 CronScheduler 같은 런타임
컴포넌트를 호출한다.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from simpleclaw.daemon.models import ActionType
from simpleclaw.proactive.models import (
    OpportunityStatus,
    ProactiveOpportunity,
    SuggestedActionKind,
)
from simpleclaw.proactive.store import OpportunityStore

_SECRET_RE = re.compile(r"(?i)\b(token|api[_-]?key|secret|password)\s*([:=])\s*([^\s]+)")


def redact_secret_text(text: str) -> str:
    """오류 요약에 섞인 흔한 secret 패턴을 제거한다."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)} [REDACTED]" if m.group(2) == ":" else f"{m.group(1)}{m.group(2)}[REDACTED]", text)


class ProactiveActionExecutor:
    """사용자가 승인한 SuggestedAction을 실제 런타임 side effect로 실행한다."""

    def __init__(
        self,
        *,
        store: OpportunityStore,
        cron_scheduler=None,
        create_cron_enabled: bool = True,
    ) -> None:
        """store와 선택적 CronScheduler를 주입한다. 생성자는 side effect를 만들지 않는다."""
        self._store = store
        self._cron_scheduler = cron_scheduler
        self._create_cron_enabled = create_cron_enabled

    async def execute(
        self,
        opportunity_id: str,
        action: str = "accept",
        payload: dict[str, Any] | None = None,
    ) -> str:
        """callback action을 처리하고 사용자에게 돌려줄 짧은 결과 문구를 반환한다."""
        opportunity = self._store.get(opportunity_id)
        if opportunity is None:
            return "제안을 찾을 수 없습니다."
        if action in {"dismiss"}:
            self._store.mark_dismissed(opportunity_id)
            return "제안을 거절했어요."
        if action in {"snooze"}:
            self._store.mark_snoozed(opportunity_id)
            return "나중에 다시 볼게요."
        if action == "edit_schedule":
            return self._edit_schedule(opportunity, payload or {})
        if action != "accept":
            return "지원하지 않는 작업입니다."
        try:
            if opportunity.suggested_action.kind != SuggestedActionKind.CREATE_CRON:
                self._store.update_status(opportunity_id, OpportunityStatus.ACCEPTED)
                return "승인했지만 이 action은 아직 준비 중입니다."
            return self._create_cron(opportunity)
        except Exception as exc:  # noqa: BLE001 — callback UX와 audit 상태를 보존한다.
            summary = redact_secret_text(str(exc))[:1000]
            self._update_opportunity(opportunity, status=OpportunityStatus.FAILED, error_summary=summary)
            return f"실행에 실패했어요: {summary}"

    def _create_cron(self, opportunity: ProactiveOpportunity) -> str:
        """create_cron payload를 검증하고 CronScheduler에 idempotent하게 등록한다."""
        if self._cron_scheduler is None:
            raise RuntimeError("CronScheduler is not configured")
        if not self._create_cron_enabled:
            raise RuntimeError("create_cron action is disabled")
        payload = opportunity.suggested_action.payload
        name = str(payload.get("name") or f"proactive-{opportunity.id}")
        cron_expression = str(payload.get("cron_expression") or payload.get("schedule") or "").strip()
        action_reference = str(payload.get("action_reference") or payload.get("prompt") or "").strip()
        action_type = self._coerce_action_type(payload.get("action_type", "prompt"))
        if not cron_expression:
            raise ValueError("cron_expression is required")
        if not action_reference:
            raise ValueError("action_reference is required")

        existing = self._cron_scheduler.get_job(name)
        if existing is not None:
            same = (
                existing.cron_expression == cron_expression
                and existing.action_type == action_type
                and existing.action_reference == action_reference
            )
            if same:
                self._update_opportunity(opportunity, status=OpportunityStatus.EXECUTED)
                return f"이미 등록된 cron job이에요: {name}"
            raise ValueError(f"cron job already exists with different definition: {name}")

        self._cron_scheduler.add_job(
            name,
            cron_expression,
            action_type,
            action_reference,
        )
        self._update_opportunity(opportunity, status=OpportunityStatus.EXECUTED)
        return f"등록했어요: {name}"

    def _edit_schedule(self, opportunity: ProactiveOpportunity, payload: dict[str, Any]) -> str:
        """사용자가 선택한 새 스케줄을 pending action payload에 반영한다."""
        next_expr = str(payload.get("cron_expression") or payload.get("schedule") or "").strip()
        if not next_expr:
            return "원하는 시간을 답장으로 보내주세요."
        opportunity.suggested_action.payload["cron_expression"] = next_expr
        self._update_opportunity(opportunity, status=OpportunityStatus.PENDING)
        return "시간을 변경했어요. 등록을 누르면 새 시간으로 저장됩니다."

    def _update_opportunity(self, opportunity: ProactiveOpportunity, **changes: Any) -> None:
        """단일 opportunity를 변경해 JSONL 저장소에 다시 쓴다."""
        items = self._store.load()
        now = datetime.now()
        for idx, item in enumerate(items):
            if item.id == opportunity.id:
                for key, value in changes.items():
                    setattr(opportunity, key, value)
                opportunity.updated_at = now
                items[idx] = opportunity
                self._store.save_all(items)
                return

    def _coerce_action_type(self, value: object) -> ActionType:
        """payload 문자열을 CronScheduler ActionType으로 정규화한다."""
        if isinstance(value, ActionType):
            return value
        return ActionType(str(value or "prompt"))
