"""Subagent review 결과의 구조화 ledger (BIZ-440).

상위 agent 가 참고용으로 띄운 subagent/parallel review 의 결과를 required /
optional gate 구분과 함께 JSONL 로 남긴다. 운영 정책(이미 instruction 에 반영됨)을
데이터로 강제하기 위한 계층이다:

- ``required`` gate 는 완료 전까지 merge/release/deploy 판정을 막는다
  (``can_merge()`` 가 False).
- ``optional`` review 는 애초에 merge blocking 이 아니며, deadline 이후 늦게
  도착한 결과(late result)는 parent issue/merge/deploy 상태를 자동으로 뒤집지
  않는다 — follow-up issue id 또는 follow-up note 를 record 에 연결하는 경로만
  제공한다.

설계 결정:

- **JSONL + 전체 rewrite.** record 수가 이슈 단위 review 규모(수십 건)라 append
  전용 로그보다 skill/recipe suggestion store 와 같은 "load → mutate →
  tmp+replace 원자적 저장" 패턴을 재사용한다. 부분 손상 라인은 경고 후 건너뛴다.
- **merge_blocking 은 생성 시 gate_kind 에서 파생해 저장.** 이후 정책이 바뀌어도
  과거 record 의 판정 근거가 그대로 남는다(감사 가능성).
- **parent 상태를 만지는 API 를 아예 두지 않는다.** mark_late / link_followup 은
  해당 record 필드만 바꾼다 — "이미 merge/deploy 된 판정을 자동으로 뒤집지
  않는다" 를 코드 경계로 보장한다.
- **시간 주입.** now 콜백을 받아 late 판정/retention 을 테스트에서 결정적으로
  재현한다.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]

# record 상태 — running(진행 중) → completed(결과 도착) 또는 late(마감 후 명시
# 지연 처리). late 는 optional review 의 "늦게 왔거나 아직 안 왔다" 를 기록하는
# 상태이며 parent 판정과는 독립이다.
REVIEW_STATUS_RUNNING = "running"
REVIEW_STATUS_COMPLETED = "completed"
REVIEW_STATUS_LATE = "late"
VALID_REVIEW_STATUSES = (
    REVIEW_STATUS_RUNNING,
    REVIEW_STATUS_COMPLETED,
    REVIEW_STATUS_LATE,
)


class ReviewGateKind(str, Enum):
    """subagent review 가 merge 판정에 미치는 등급."""

    REQUIRED = "required"
    OPTIONAL = "optional"


class SubagentLedgerError(Exception):
    """ledger 조작 중 발생한 검증/조회 오류."""


def _utcnow() -> datetime:
    """기본 now 제공자 — 테스트는 ledger 에 now 콜백을 주입해 고정한다."""
    return datetime.now(UTC)


def _parse_iso(value: object) -> datetime | None:
    """ISO8601 문자열을 timezone-aware datetime 으로 파싱한다.

    naive 값은 UTC 로 간주하고 ``Z`` 접미사도 허용한다. 파싱 실패는 None —
    깨진 timestamp 하나가 ledger 조회 전체를 죽이면 안 된다.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@dataclass
class SubagentReviewRecord:
    """subagent review 한 건의 구조화 snapshot."""

    id: str
    issue_id: str
    parent_run_id: str
    spawned_by: str
    purpose: str
    gate_kind: ReviewGateKind
    status: str = REVIEW_STATUS_RUNNING
    started_at: str | None = None
    deadline_at: str | None = None
    completed_at: str | None = None
    merge_blocking: bool = False
    result_summary: str = ""
    finding_severity: str | None = None
    followup_issue_id: str | None = None
    followup_note: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        issue_id: str,
        spawned_by: str,
        purpose: str,
        gate_kind: ReviewGateKind | str,
        parent_run_id: str = "",
        deadline_at: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        now: NowFn | None = None,
    ) -> SubagentReviewRecord:
        """새 review record 를 만든다.

        merge_blocking 은 여기서 gate_kind 로부터 한 번만 파생한다 — required
        gate 는 생성 즉시 merge blocking 이고, optional 은 어떤 시점에도
        blocking 이 아니다.
        """
        kind = ReviewGateKind(gate_kind)
        started = (now or _utcnow)().isoformat()
        return cls(
            id=uuid.uuid4().hex[:12],
            issue_id=issue_id,
            parent_run_id=parent_run_id,
            spawned_by=spawned_by,
            purpose=purpose,
            gate_kind=kind,
            status=REVIEW_STATUS_RUNNING,
            started_at=started,
            deadline_at=deadline_at,
            merge_blocking=(kind is ReviewGateKind.REQUIRED),
            source_metadata=dict(source_metadata or {}),
        )

    @property
    def is_completed(self) -> bool:
        return self.status == REVIEW_STATUS_COMPLETED

    def is_late(self, now: datetime) -> bool:
        """deadline 이 지났는데 완료되지 않았으면 late 로 본다.

        명시적으로 ``late`` 상태가 된 record 도 포함한다 — mark_late 이전/이후
        어느 쪽에서 조회하든 같은 집합이 나와야 운영자가 혼동하지 않는다.
        """
        if self.status == REVIEW_STATUS_LATE:
            return True
        if self.is_completed:
            return False
        deadline = _parse_iso(self.deadline_at)
        return deadline is not None and deadline < now

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "issue_id": self.issue_id,
            "parent_run_id": self.parent_run_id,
            "spawned_by": self.spawned_by,
            "purpose": self.purpose,
            "gate_kind": self.gate_kind.value,
            "status": self.status,
            "started_at": self.started_at,
            "deadline_at": self.deadline_at,
            "completed_at": self.completed_at,
            "merge_blocking": self.merge_blocking,
            "result_summary": self.result_summary,
            "finding_severity": self.finding_severity,
            "followup_issue_id": self.followup_issue_id,
            "followup_note": self.followup_note,
            "source_metadata": self.source_metadata,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubagentReviewRecord:
        """저장분을 관대하게 복원한다 — 알 수 없는 값은 안전한 기본으로 정규화."""
        try:
            kind = ReviewGateKind(str(raw.get("gate_kind") or "optional"))
        except ValueError:
            kind = ReviewGateKind.OPTIONAL
        status = str(raw.get("status") or REVIEW_STATUS_RUNNING)
        if status not in VALID_REVIEW_STATUSES:
            status = REVIEW_STATUS_RUNNING
        source_metadata = raw.get("source_metadata")
        if not isinstance(source_metadata, dict):
            source_metadata = {}
        return cls(
            id=str(raw.get("id") or ""),
            issue_id=str(raw.get("issue_id") or ""),
            parent_run_id=str(raw.get("parent_run_id") or ""),
            spawned_by=str(raw.get("spawned_by") or ""),
            purpose=str(raw.get("purpose") or ""),
            gate_kind=kind,
            status=status,
            started_at=raw.get("started_at"),
            deadline_at=raw.get("deadline_at"),
            completed_at=raw.get("completed_at"),
            merge_blocking=bool(raw.get("merge_blocking", False)),
            result_summary=str(raw.get("result_summary") or ""),
            finding_severity=(
                str(raw["finding_severity"]) if raw.get("finding_severity") else None
            ),
            followup_issue_id=(
                str(raw["followup_issue_id"]) if raw.get("followup_issue_id") else None
            ),
            followup_note=(
                str(raw["followup_note"]) if raw.get("followup_note") else None
            ),
            source_metadata=source_metadata,
        )


class SubagentReviewLedger:
    """JSONL subagent review ledger 저장소.

    skill/recipe suggestion store 와 같은 "load → mutate → 원자적 rewrite"
    패턴을 쓴다. retention_days 가 지정되면 저장 시 완료(terminal) 상태이면서
    보존 기간을 넘긴 record 만 정리한다 — required 미완료 record 는 절대
    정리하지 않는다(merge gate 증거 보존).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: int | None = None,
        now: NowFn | None = None,
    ) -> None:
        self._path = Path(path).expanduser()
        self._retention_days = retention_days
        self._now = now or _utcnow

    @property
    def path(self) -> Path:
        return self._path

    # -- 저장/로드 -----------------------------------------------------

    def load(self) -> list[SubagentReviewRecord]:
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read review ledger %s: %s", self._path, exc)
            return []
        records: list[SubagentReviewRecord] = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(SubagentReviewRecord.from_dict(item))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed review ledger line %d: %s", line_no, exc
                )
        return records

    def _save_all(self, records: list[SubagentReviewRecord]) -> None:
        """tmp 파일에 전체를 쓰고 rename 으로 교체하는 원자적 저장."""
        records = self._apply_retention(records)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self._path)

    def _apply_retention(
        self, records: list[SubagentReviewRecord]
    ) -> list[SubagentReviewRecord]:
        """보존 기간을 넘긴 completed record 를 정리한다.

        미완료 record(running/late)는 gate 증거 또는 follow-up 대기 상태일 수
        있으므로 기간과 무관하게 남긴다.
        """
        if self._retention_days is None or self._retention_days <= 0:
            return records
        cutoff = self._now() - timedelta(days=self._retention_days)
        kept: list[SubagentReviewRecord] = []
        for record in records:
            if record.is_completed:
                completed = _parse_iso(record.completed_at)
                if completed is not None and completed < cutoff:
                    continue
            kept.append(record)
        return kept

    # -- 생성/갱신 -----------------------------------------------------

    def start(
        self,
        *,
        issue_id: str,
        spawned_by: str,
        purpose: str,
        gate_kind: ReviewGateKind | str,
        parent_run_id: str = "",
        deadline_at: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> SubagentReviewRecord:
        """새 review record 를 append 한다."""
        if not str(issue_id).strip():
            raise SubagentLedgerError("issue_id 는 비어 있을 수 없습니다.")
        record = SubagentReviewRecord.new(
            issue_id=str(issue_id).strip(),
            spawned_by=str(spawned_by).strip(),
            purpose=str(purpose).strip(),
            gate_kind=gate_kind,
            parent_run_id=str(parent_run_id).strip(),
            deadline_at=deadline_at,
            source_metadata=source_metadata,
            now=self._now,
        )
        records = self.load()
        records.append(record)
        self._save_all(records)
        return record

    def _update(
        self,
        record_id: str,
        mutate: Callable[[SubagentReviewRecord], None],
    ) -> SubagentReviewRecord:
        """record 하나를 찾아 mutate 적용 후 저장한다. 없으면 오류."""
        records = self.load()
        for idx, record in enumerate(records):
            if record.id == record_id:
                mutate(record)
                records[idx] = record
                self._save_all(records)
                return record
        raise SubagentLedgerError(f"record '{record_id}' 를 찾을 수 없습니다.")

    def complete(
        self,
        record_id: str,
        *,
        result_summary: str = "",
        finding_severity: str | None = None,
    ) -> SubagentReviewRecord:
        """review 결과 도착을 기록한다 (완료 시각/요약/심각도)."""

        def _mutate(record: SubagentReviewRecord) -> None:
            record.status = REVIEW_STATUS_COMPLETED
            record.completed_at = self._now().isoformat()
            record.result_summary = str(result_summary or "")
            record.finding_severity = (
                str(finding_severity).strip().lower() if finding_severity else None
            )

        return self._update(record_id, _mutate)

    def mark_late(self, record_id: str) -> SubagentReviewRecord:
        """record 를 명시적 late 상태로 표시한다.

        이 record 필드 외에는 아무것도 바꾸지 않는다 — parent issue 의
        done/merge/deploy 판정은 이 ledger 의 관할이 아니다.
        """

        def _mutate(record: SubagentReviewRecord) -> None:
            if record.is_completed:
                raise SubagentLedgerError(
                    f"record '{record_id}' 는 이미 완료돼 late 로 표시할 수 없습니다."
                )
            record.status = REVIEW_STATUS_LATE

        return self._update(record_id, _mutate)

    def link_followup(
        self,
        record_id: str,
        *,
        followup_issue_id: str | None = None,
        note: str | None = None,
    ) -> SubagentReviewRecord:
        """late/optional finding 을 follow-up issue id 또는 draft note 로 연결한다.

        실제 issue 생성 자동화는 v1 범위 밖이다 — 운영자가 note/draft 를 보고
        직접 issue 를 만든 뒤 id 를 연결하는 흐름을 전제로 한다.
        """
        if not (followup_issue_id or (note and note.strip())):
            raise SubagentLedgerError(
                "followup_issue_id 또는 note 중 하나는 필요합니다."
            )

        def _mutate(record: SubagentReviewRecord) -> None:
            if followup_issue_id:
                record.followup_issue_id = str(followup_issue_id).strip()
            if note and note.strip():
                record.followup_note = note.strip()

        return self._update(record_id, _mutate)

    # -- 조회 ----------------------------------------------------------

    def get(self, record_id: str) -> SubagentReviewRecord | None:
        return next((r for r in self.load() if r.id == record_id), None)

    def list_by_issue(self, issue_id: str) -> list[SubagentReviewRecord]:
        """issue 단위 record 목록 (started_at 순서 보존)."""
        target = str(issue_id).strip()
        return [r for r in self.load() if r.issue_id == target]

    def late_records(
        self, issue_id: str | None = None
    ) -> list[SubagentReviewRecord]:
        """deadline 이 지났는데 완료되지 않은 record 를 모은다."""
        now = self._now()
        records = self.list_by_issue(issue_id) if issue_id else self.load()
        return [r for r in records if r.is_late(now)]

    def blocking_records(self, issue_id: str) -> list[SubagentReviewRecord]:
        """merge 를 막고 있는(=required 이면서 미완료) record 목록."""
        return [
            r
            for r in self.list_by_issue(issue_id)
            if r.merge_blocking and not r.is_completed
        ]

    def can_merge(self, issue_id: str) -> bool:
        """required gate 가 전부 완료됐을 때만 True.

        optional record 는 완료 여부/지각 여부와 무관하게 판정에 관여하지
        않는다 — late optional finding 은 follow-up issue 경로로만 처리된다.
        """
        return not self.blocking_records(issue_id)

    def gate_evidence(self, issue_id: str) -> dict[str, Any]:
        """required gate 상태를 verification evidence 입력 형태로 요약한다.

        BIZ-441 verification ledger 가 subagent required gate 미완료를
        ``subagent_gate`` stage 의 done block evidence 로 기록할 수 있게 하는
        read helper 다. verification 모듈에 의존하지 않도록 plain dict 로
        반환한다 — status 문자열은 VerificationStatus 값과 일치한다.
        """
        blocking = self.blocking_records(issue_id)
        if blocking:
            purposes = ", ".join(r.purpose or r.id for r in blocking)
            summary = (
                f"required subagent review {len(blocking)}건 미완료: {purposes}"
            )
            status = "pending"
        else:
            summary = "required subagent review gate 전부 완료"
            status = "passed"
        return {
            "stage": "subagent_gate",
            "status": status,
            "summary": summary,
            "source": "subagent_ledger",
            "blocking_record_ids": [r.id for r in blocking],
        }


__all__ = [
    "REVIEW_STATUS_COMPLETED",
    "REVIEW_STATUS_LATE",
    "REVIEW_STATUS_RUNNING",
    "VALID_REVIEW_STATUSES",
    "ReviewGateKind",
    "SubagentLedgerError",
    "SubagentReviewLedger",
    "SubagentReviewRecord",
]
