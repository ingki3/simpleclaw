"""Proactive Opportunity Queue의 JSONL 저장소.

후보 생성과 사용자 발송을 분리하기 위해 detector는 이 저장소에 pending 후보만
적재한다. 저장은 기존 dreaming suggestion queue처럼 전체 로드 후 tmp+replace로
원자적으로 기록하며, pending 중복 제거는 cooldown_key를 기준으로 수행한다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from simpleclaw.proactive.models import (
    OpportunityStatus,
    ProactiveOpportunity,
    TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)


class OpportunityStore:
    """JSONL 기반 proactive opportunity 저장소."""

    def __init__(self, path: str | Path) -> None:
        """저장 파일 경로만 보관하고 실제 IO는 호출 시점까지 미룬다."""
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        """외부 검증과 테스트가 저장 경로를 확인할 수 있게 반환한다."""
        return self._path

    def load(self) -> list[ProactiveOpportunity]:
        """JSONL 전체를 읽어 opportunity 목록으로 복원한다."""
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read opportunity queue %s: %s", self._path, exc)
            return []
        out: list[ProactiveOpportunity] = []
        for line_no, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(ProactiveOpportunity.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed opportunity line %d in %s: %s",
                    line_no,
                    self._path,
                    exc,
                )
        return out

    def save_all(self, items: list[ProactiveOpportunity]) -> None:
        """전체 목록을 tmp 파일에 쓴 뒤 replace하여 부분 쓰기를 피한다."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item.to_dict(), ensure_ascii=False))
                f.write("\n")
        tmp_path.replace(self._path)

    def list_all(self) -> list[ProactiveOpportunity]:
        """감사/테스트 목적으로 terminal row까지 포함한 전체 목록을 반환한다."""
        return self.load()

    def list_pending(self, *, now: datetime | None = None) -> list[ProactiveOpportunity]:
        """만료되지 않은 pending 후보를 최신순으로 반환한다."""
        ts = now or datetime.now()
        items = [
            item
            for item in self.load()
            if item.status == OpportunityStatus.PENDING and not item.is_expired(ts)
        ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items

    def get(self, opportunity_id: str) -> ProactiveOpportunity | None:
        """ID로 단일 opportunity를 조회한다."""
        for item in self.load():
            if item.id == opportunity_id:
                return item
        return None

    def upsert_pending_by_cooldown_key(
        self, opportunity: ProactiveOpportunity
    ) -> ProactiveOpportunity:
        """같은 cooldown_key의 pending row를 갱신하거나 새 pending row를 추가한다.

        terminal row는 audit trail이므로 그대로 두며, 새 후보가 들어오면 별도의
        pending row를 만든다. cooldown_key가 비어 있으면 idempotency를 적용할 수
        없어 항상 새 row로 저장한다.
        """
        items = self.load()
        now = datetime.now()
        opportunity.status = OpportunityStatus.PENDING
        opportunity.updated_at = now
        key = opportunity.cooldown_key.strip()
        if key:
            for idx, item in enumerate(items):
                if item.status == OpportunityStatus.PENDING and item.cooldown_key == key:
                    opportunity.id = item.id
                    opportunity.created_at = item.created_at
                    opportunity.presented_count = item.presented_count
                    opportunity.last_presented_at = item.last_presented_at
                    items[idx] = opportunity
                    self.save_all(items)
                    return opportunity
        if opportunity.created_at is None:
            opportunity.created_at = now
        items.append(opportunity)
        self.save_all(items)
        return opportunity

    def update_status(
        self,
        opportunity_id: str,
        status: OpportunityStatus | str,
        *,
        now: datetime | None = None,
    ) -> ProactiveOpportunity | None:
        """상태를 변경하고 발송 시각/횟수 같은 상태 부가값을 함께 갱신한다."""
        next_status = OpportunityStatus(str(status))
        ts = now or datetime.now()
        items = self.load()
        for idx, item in enumerate(items):
            if item.id != opportunity_id:
                continue
            item.status = next_status
            item.updated_at = ts
            if next_status == OpportunityStatus.SENT:
                item.last_presented_at = ts
                item.presented_count += 1
            items[idx] = item
            self.save_all(items)
            return item
        return None

    def mark_sent(
        self, opportunity_id: str, *, now: datetime | None = None
    ) -> ProactiveOpportunity | None:
        """후보가 사용자에게 노출되었음을 기록한다."""
        return self.update_status(opportunity_id, OpportunityStatus.SENT, now=now)

    def mark_accepted(self, opportunity_id: str) -> ProactiveOpportunity | None:
        """사용자가 후보를 수락했음을 기록한다."""
        return self.update_status(opportunity_id, OpportunityStatus.ACCEPTED)

    def mark_dismissed(self, opportunity_id: str) -> ProactiveOpportunity | None:
        """사용자가 후보를 거절했음을 기록한다."""
        return self.update_status(opportunity_id, OpportunityStatus.DISMISSED)

    def mark_snoozed(self, opportunity_id: str) -> ProactiveOpportunity | None:
        """사용자가 후보를 나중에 보기로 미뤘음을 기록한다."""
        return self.update_status(opportunity_id, OpportunityStatus.SNOOZED)

    def expire_old(self, *, now: datetime | None = None) -> int:
        """만료 시각이 지난 pending 후보를 expired로 전환하고 개수를 반환한다."""
        ts = now or datetime.now()
        items = self.load()
        changed = 0
        for item in items:
            if item.status == OpportunityStatus.PENDING and item.is_expired(ts):
                item.status = OpportunityStatus.EXPIRED
                item.updated_at = ts
                changed += 1
        if changed:
            self.save_all(items)
        return changed

    def count_sent_since(self, since: datetime) -> int:
        """특정 시각 이후 발송된 후보 수를 daily budget 계산용으로 센다."""
        return sum(
            1
            for item in self.load()
            if item.status == OpportunityStatus.SENT
            and item.last_presented_at is not None
            and item.last_presented_at >= since
        )

    def last_terminal_for_cooldown_key(
        self, cooldown_key: str
    ) -> ProactiveOpportunity | None:
        """동일 cooldown_key의 최근 terminal row를 찾아 cooldown 판정에 제공한다."""
        key = cooldown_key.strip()
        if not key:
            return None
        rows = [
            item
            for item in self.load()
            if item.cooldown_key == key and item.status in TERMINAL_STATUSES
        ]
        if not rows:
            return None
        return max(rows, key=lambda item: item.updated_at)
