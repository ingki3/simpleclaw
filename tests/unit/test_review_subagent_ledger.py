"""SubagentReviewLedger 단위 테스트 (BIZ-440).

required/optional gate 구분, merge blocking 판정, late record 조회, follow-up
연결, JSONL 직렬화/보존 정책을 검증한다. 시간은 now 콜백 주입으로 고정한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from simpleclaw.review.subagent_ledger import (
    REVIEW_STATUS_COMPLETED,
    REVIEW_STATUS_LATE,
    REVIEW_STATUS_RUNNING,
    ReviewGateKind,
    SubagentLedgerError,
    SubagentReviewLedger,
    SubagentReviewRecord,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


class _Clock:
    """테스트에서 시간을 결정적으로 진행시키는 now 콜백."""

    def __init__(self, start: datetime = _NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def ledger(tmp_path, clock) -> SubagentReviewLedger:
    return SubagentReviewLedger(tmp_path / "ledger.jsonl", now=clock)


def _start(ledger, *, gate_kind="optional", issue_id="BIZ-1", **overrides):
    params = {
        "issue_id": issue_id,
        "spawned_by": "parent-agent",
        "purpose": "security review",
        "gate_kind": gate_kind,
    }
    params.update(overrides)
    return ledger.start(**params)


# ---------------------------------------------------------------------------
# gate kind / merge blocking
# ---------------------------------------------------------------------------


def test_required_gate_record_is_merge_blocking(ledger):
    record = _start(ledger, gate_kind="required")

    assert record.gate_kind is ReviewGateKind.REQUIRED
    assert record.merge_blocking is True
    assert record.status == REVIEW_STATUS_RUNNING


def test_optional_record_is_not_merge_blocking_even_before_completion(ledger):
    record = _start(ledger, gate_kind="optional")

    assert record.merge_blocking is False
    # optional review 만 있으면 미완료여도 merge 가능.
    assert ledger.can_merge("BIZ-1") is True


def test_can_merge_blocked_while_required_incomplete(ledger):
    required = _start(ledger, gate_kind="required")
    _start(ledger, gate_kind="optional")

    assert ledger.can_merge("BIZ-1") is False
    assert [r.id for r in ledger.blocking_records("BIZ-1")] == [required.id]

    ledger.complete(required.id, result_summary="no findings")
    assert ledger.can_merge("BIZ-1") is True
    assert ledger.blocking_records("BIZ-1") == []


def test_can_merge_is_issue_scoped(ledger):
    _start(ledger, gate_kind="required", issue_id="BIZ-1")

    assert ledger.can_merge("BIZ-1") is False
    assert ledger.can_merge("BIZ-2") is True


# ---------------------------------------------------------------------------
# complete / late / follow-up
# ---------------------------------------------------------------------------


def test_complete_stores_summary_severity_and_timestamp(ledger, clock):
    record = _start(ledger)
    clock.advance(minutes=30)

    updated = ledger.complete(
        record.id, result_summary="found race condition", finding_severity="Major"
    )

    assert updated.status == REVIEW_STATUS_COMPLETED
    assert updated.result_summary == "found race condition"
    assert updated.finding_severity == "major"
    assert updated.completed_at == clock.current.isoformat()


def test_optional_incomplete_past_deadline_is_late(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    record = _start(ledger, gate_kind="optional", deadline_at=deadline)

    # 마감 전에는 late 가 아니다.
    assert ledger.late_records("BIZ-1") == []

    clock.advance(hours=2)
    late = ledger.late_records("BIZ-1")
    assert [r.id for r in late] == [record.id]


def test_completed_record_is_not_late_even_past_deadline(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    record = _start(ledger, deadline_at=deadline)
    ledger.complete(record.id, result_summary="ok")

    clock.advance(hours=2)
    assert ledger.late_records("BIZ-1") == []


def test_mark_late_sets_status_without_touching_other_records(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    late_target = _start(ledger, gate_kind="optional", deadline_at=deadline)
    sibling = _start(ledger, gate_kind="required")
    clock.advance(hours=2)

    updated = ledger.mark_late(late_target.id)

    assert updated.status == REVIEW_STATUS_LATE
    assert updated.completed_at is None
    # 다른 record 는 어떤 필드도 변경되지 않는다.
    reloaded_sibling = ledger.get(sibling.id)
    assert reloaded_sibling.to_dict() == sibling.to_dict()


def test_mark_late_rejects_completed_record(ledger):
    record = _start(ledger)
    ledger.complete(record.id)

    with pytest.raises(SubagentLedgerError, match="이미 완료"):
        ledger.mark_late(record.id)


def test_late_optional_finding_links_followup_issue_id(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    record = _start(ledger, gate_kind="optional", deadline_at=deadline)
    clock.advance(hours=2)
    ledger.mark_late(record.id)

    updated = ledger.link_followup(
        record.id, followup_issue_id="BIZ-999", note="late security finding"
    )

    assert updated.followup_issue_id == "BIZ-999"
    assert updated.followup_note == "late security finding"
    # follow-up 연결 후에도 late 조회에는 계속 나타난다.
    assert record.id in {r.id for r in ledger.late_records("BIZ-1")}


def test_link_followup_requires_issue_id_or_note(ledger):
    record = _start(ledger)

    with pytest.raises(SubagentLedgerError, match="followup_issue_id 또는 note"):
        ledger.link_followup(record.id)


def test_update_missing_record_raises(ledger):
    with pytest.raises(SubagentLedgerError, match="찾을 수 없습니다"):
        ledger.complete("no-such-id")


# ---------------------------------------------------------------------------
# 직렬화 / 저장소
# ---------------------------------------------------------------------------


def test_record_roundtrips_through_dict():
    record = SubagentReviewRecord.new(
        issue_id="BIZ-1",
        spawned_by="parent",
        purpose="perf review",
        gate_kind="required",
        parent_run_id="run-7",
        deadline_at="2026-07-16T13:00:00+00:00",
        source_metadata={"model": "gemini", "tokens": 1200},
        now=lambda: _NOW,
    )

    restored = SubagentReviewRecord.from_dict(record.to_dict())

    assert restored == record


def test_records_persist_across_ledger_instances(tmp_path, clock):
    path = tmp_path / "ledger.jsonl"
    first = SubagentReviewLedger(path, now=clock)
    record = _start(first, gate_kind="required")

    second = SubagentReviewLedger(path, now=clock)
    loaded = second.list_by_issue("BIZ-1")

    assert [r.id for r in loaded] == [record.id]
    assert loaded[0].merge_blocking is True


def test_load_skips_malformed_lines(tmp_path, clock):
    path = tmp_path / "ledger.jsonl"
    ledger = SubagentReviewLedger(path, now=clock)
    record = _start(ledger)
    with path.open("a", encoding="utf-8") as f:
        f.write("{not json}\n")

    assert [r.id for r in ledger.load()] == [record.id]


def test_from_dict_normalizes_unknown_gate_and_status():
    restored = SubagentReviewRecord.from_dict(
        {"id": "x", "issue_id": "BIZ-1", "gate_kind": "mystery", "status": "weird"}
    )

    assert restored.gate_kind is ReviewGateKind.OPTIONAL
    assert restored.status == REVIEW_STATUS_RUNNING


def test_list_by_issue_returns_only_that_issue(ledger):
    a = _start(ledger, issue_id="BIZ-1")
    _start(ledger, issue_id="BIZ-2")

    assert [r.id for r in ledger.list_by_issue("BIZ-1")] == [a.id]


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------


def test_retention_prunes_old_completed_but_keeps_incomplete(tmp_path, clock):
    ledger = SubagentReviewLedger(
        tmp_path / "ledger.jsonl", retention_days=30, now=clock
    )
    old_completed = _start(ledger, gate_kind="optional")
    ledger.complete(old_completed.id, result_summary="done long ago")
    old_required_incomplete = _start(ledger, gate_kind="required")

    clock.advance(days=60)
    # 저장 시점에 retention 이 적용된다 — 새 record 하나를 추가해 rewrite 유도.
    fresh = _start(ledger, gate_kind="optional")

    remaining_ids = {r.id for r in ledger.load()}
    assert old_completed.id not in remaining_ids
    # required 미완료 record 는 기간과 무관하게 보존된다.
    assert old_required_incomplete.id in remaining_ids
    assert fresh.id in remaining_ids


def test_saved_jsonl_lines_are_valid_json(tmp_path, clock):
    path = tmp_path / "ledger.jsonl"
    ledger = SubagentReviewLedger(path, now=clock)
    _start(ledger)
    _start(ledger, gate_kind="required")

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        assert isinstance(json.loads(line), dict)
