"""Proactive opportunity를 Telegram 제안으로 노출하는 presenter.

Detector가 큐에 적재한 pending 후보를 TPOPolicyEngine으로 다시 평가한 뒤,
TelegramBot으로 낮은 압박의 승인/거절 메시지만 발송한다. cron 등록 같은
side effect는 이 모듈이 직접 수행하지 않고 callback 승인 후 action executor로 넘긴다.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from simpleclaw.proactive.models import (
    OpportunityStatus,
    PolicyDecisionAction,
    ProactiveOpportunity,
    TPOContext,
)
from simpleclaw.proactive.policy import TPOPolicyEngine
from simpleclaw.proactive.store import OpportunityStore

logger = logging.getLogger(__name__)

PROACTIVE_CALLBACK_PREFIX = "pc"
PROACTIVE_ACTIONS = {"accept", "edit", "snooze", "dismiss", "edit_schedule"}


def build_proactive_callback_data(action: str, opportunity_id: str) -> str:
    """Telegram 64 byte 제한 안에 들어가는 proactive callback_data를 만든다."""
    data = f"{PROACTIVE_CALLBACK_PREFIX}:{action}:{opportunity_id}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("proactive callback_data exceeds Telegram 64 byte limit")
    return data


def parse_proactive_callback_data(data: str) -> tuple[str, str] | None:
    """proactive callback_data를 (action, opportunity_id)로 복원한다."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != PROACTIVE_CALLBACK_PREFIX:
        return None
    action, opportunity_id = parts[1], parts[2]
    if action not in PROACTIVE_ACTIONS or not opportunity_id:
        return None
    return action, opportunity_id


def format_proactive_message(opportunity: ProactiveOpportunity) -> str:
    """사용자에게 보낼 evidence/action/선택지 포함 제안 문구를 만든다."""
    lines = ["💡 제안", opportunity.message_draft or opportunity.title]
    if opportunity.evidence:
        lines.append("")
        lines.append("왜 제안하냐면:")
        for evidence in opportunity.evidence[:3]:
            lines.append(f"- {evidence}")
    if opportunity.suggested_action.label:
        lines.append("")
        lines.append(f"제안 action: {opportunity.suggested_action.label}")
    lines.append("")
    lines.append("선택: 등록 / 시간 변경 / 나중에 / 아니요")
    return "\n".join(lines)


class ProactivePresenter:
    """pending opportunity를 정책 평가 후 Telegram으로 제안한다."""

    def __init__(
        self,
        *,
        store: OpportunityStore,
        telegram_bot,
        chat_id: int | None,
        policy: TPOPolicyEngine | None = None,
        config: dict[str, Any] | None = None,
        action_executor=None,
    ) -> None:
        """저장소/정책/채널과 런타임 설정을 주입받아 presenter를 구성한다."""
        self._store = store
        self._telegram_bot = telegram_bot
        self._chat_id = chat_id
        self._policy = policy or TPOPolicyEngine()
        self._config = config or {}
        self._action_executor = action_executor

    async def tick(self, *, now: datetime | None = None) -> int:
        """pending 후보를 평가하고 발송 가능한 항목 수를 반환한다."""
        if self._chat_id is None:
            return 0
        ts = now or datetime.now()
        self._store.expire_old(now=ts)
        sent = 0
        for opportunity in self._store.list_pending(now=ts):
            context = self._build_context(opportunity, ts)
            decision = self._policy.evaluate(opportunity, context)
            if decision.action not in {
                PolicyDecisionAction.SEND_NOW,
                PolicyDecisionAction.NEEDS_USER_APPROVAL,
            }:
                logger.debug(
                    "Proactive opportunity %s not sent: %s",
                    opportunity.id,
                    decision.to_dict(),
                )
                continue
            message = format_proactive_message(opportunity)
            await self._telegram_bot.send_proactive_opportunity(
                chat_id=self._chat_id, opportunity=opportunity, text=message
            )
            self._store.mark_sent(opportunity.id, now=ts)
            sent += 1
        return sent

    async def handle_callback(
        self, action: str, opportunity_id: str, payload: dict[str, Any] | None = None
    ) -> str:
        """Telegram callback action을 store 상태 전이나 executor 호출로 처리한다."""
        if action == "dismiss":
            self._store.mark_dismissed(opportunity_id)
            return "제안을 거절했어요."
        if action == "snooze":
            self._store.mark_snoozed(opportunity_id)
            return "나중에 다시 볼게요."
        if action in {"accept", "edit", "edit_schedule"} and self._action_executor is not None:
            mapped = "edit_schedule" if action in {"edit", "edit_schedule"} else "accept"
            return await self._action_executor.execute(opportunity_id, mapped, payload)
        if action in {"edit", "edit_schedule"}:
            return "원하는 시간을 답장으로 보내주세요."
        return "알 수 없는 proactive 선택입니다."

    def _build_context(self, opportunity: ProactiveOpportunity, now: datetime) -> TPOContext:
        """store 이력을 모아 정책 엔진에 넘길 TPOContext를 만든다."""
        start_of_day = datetime.combine(now.date(), time.min)
        quiet = self._config.get("quiet_hours", {}) or {}
        last_sent_at = opportunity.last_presented_at
        last_dismissed_at = None
        same_topic = [
            item for item in self._store.load()
            if item.cooldown_key == opportunity.cooldown_key and item.id != opportunity.id
        ]
        sent_rows = [item for item in same_topic if item.last_presented_at is not None]
        dismissed_rows = [item for item in same_topic if item.status == OpportunityStatus.DISMISSED]
        if sent_rows:
            last_sent_at = max(item.last_presented_at for item in sent_rows if item.last_presented_at)
        if dismissed_rows:
            last_dismissed_at = max(item.updated_at for item in dismissed_rows)
        return TPOContext(
            now=now,
            enabled=bool(self._config.get("enabled", False)),
            mode=str(self._config.get("mode", "low")),
            quiet_hours_start=str(quiet.get("start", "23:00")),
            quiet_hours_end=str(quiet.get("end", "08:00")),
            max_messages_per_day=int(self._config.get("max_messages_per_day", 1)),
            sent_today_count=self._store.count_sent_since(start_of_day),
            topic_cooldown_days=int(self._config.get("topic_cooldown_days", 14)),
            dismissed_cooldown_days=int(self._config.get("dismissed_cooldown_days", 30)),
            min_confidence=float(self._config.get("min_confidence", 0.75)),
            last_sent_at=last_sent_at,
            last_dismissed_at=last_dismissed_at,
        )
