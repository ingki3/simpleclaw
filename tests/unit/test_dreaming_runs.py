"""DreamingRunStore 단위 테스트 (BIZ-81).

검증 범위:
    - DreamingRunRecord.to_dict / from_dict 라운드트립
    - status 분류(success/skip/error/running) 정확성
    - begin → finish 라이프사이클이 단일 행으로 합쳐지는지
    - max_records 후행 잘라내기
    - kpi_window 의 윈도우 필터링과 skip_breakdown 집계
    - error 와 skip_reason 의 상호 배타성
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from simpleclaw.memory.dreaming_runs import (
    SKIP_EMPTY_RESULTS,
    SKIP_NO_MESSAGES,
    SKIP_PREFLIGHT_FAILED,
    DreamingRunRecord,
    DreamingRunStore,
)


def test_record_roundtrip_preserves_all_fields():
    """to_dict → from_dict 가 모든 필드를 보존해야 한다 (디스크 형식 안정성)."""
    started = datetime(2026, 5, 4, 3, 0, 0)
    ended = datetime(2026, 5, 4, 3, 0, 12)
    rec = DreamingRunRecord(
        id="abc123",
        started_at=started,
        ended_at=ended,
        input_msg_count=42,
        generated_insight_count=5,
        rejected_count=1,
        error=None,
        skip_reason=None,
        details={"note": "ok"},
    )
    restored = DreamingRunRecord.from_dict(rec.to_dict())
    assert restored.id == "abc123"
    assert restored.started_at == started
    assert restored.ended_at == ended
    assert restored.input_msg_count == 42
    assert restored.generated_insight_count == 5
    assert restored.rejected_count == 1
    assert restored.details == {"note": "ok"}


def test_status_running_when_not_finished():
    rec = DreamingRunRecord()
    assert rec.status == "running"
    assert rec.duration_seconds is None


def test_status_success_when_no_error_no_skip():
    started = datetime(2026, 5, 4, 3, 0, 0)
    rec = DreamingRunRecord(
        started_at=started, ended_at=started + timedelta(seconds=8)
    )
    assert rec.status == "success"
    assert rec.duration_seconds == 8.0


def test_status_skip_when_skip_reason_present():
    rec = DreamingRunRecord(
        ended_at=datetime.now(), skip_reason=SKIP_NO_MESSAGES
    )
    assert rec.status == "skip"


def test_status_error_when_error_present():
    rec = DreamingRunRecord(ended_at=datetime.now(), error="LLM timeout")
    assert rec.status == "error"


def test_begin_finish_writes_single_row(tmp_path: Path):
    """begin 으로 행을 만들고 finish 로 같은 id 를 갱신하면 디스크에 1행만 남아야 한다."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    rec = store.begin(input_msg_count=10)
    store.finish(rec, generated_insight_count=3, rejected_count=0)

    rows = store.load()
    assert len(rows) == 1
    assert rows[0].id == rec.id
    assert rows[0].input_msg_count == 10
    assert rows[0].generated_insight_count == 3
    assert rows[0].status == "success"
    assert rows[0].ended_at is not None


def test_finish_records_skip_reason(tmp_path: Path):
    """preflight 실패 같은 정상-스킵 케이스가 status=skip 으로 남는지."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    rec = store.begin(input_msg_count=5)
    store.finish(
        rec,
        skip_reason=SKIP_PREFLIGHT_FAILED,
        details={"missing_section": "user.insights"},
    )
    last = store.last_run()
    assert last is not None
    assert last.status == "skip"
    assert last.skip_reason == SKIP_PREFLIGHT_FAILED
    assert last.details == {"missing_section": "user.insights"}


def test_finish_records_error(tmp_path: Path):
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    rec = store.begin(input_msg_count=2)
    store.finish(rec, error="ValueError: bad config")
    last = store.last_run()
    assert last is not None
    assert last.status == "error"
    assert last.error == "ValueError: bad config"


def test_error_takes_precedence_over_skip_reason(tmp_path: Path):
    """error 와 skip_reason 이 동시에 들어오면 error 가 우선되어야 한다."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    rec = store.begin()
    store.finish(rec, error="boom", skip_reason=SKIP_NO_MESSAGES)
    last = store.last_run()
    assert last is not None
    assert last.status == "error"
    assert last.error == "boom"
    assert last.skip_reason is None


def test_unknown_skip_reason_is_accepted_with_warning(tmp_path: Path, caplog):
    """포워드 호환 — 모르는 사유도 저장하되 WARN 로그를 남긴다."""
    import logging

    store = DreamingRunStore(tmp_path / "runs.jsonl")
    rec = store.begin()
    with caplog.at_level(logging.WARNING):
        store.finish(rec, skip_reason="some_future_reason")
    assert any("Unknown dreaming skip_reason" in m for m in caplog.messages)
    last = store.last_run()
    assert last is not None
    assert last.skip_reason == "some_future_reason"


def test_max_records_truncates_oldest_first(tmp_path: Path):
    """max_records 를 초과하면 오래된 행이 잘려나가야 한다 (꼬리 N개 보존)."""
    store = DreamingRunStore(tmp_path / "runs.jsonl", max_records=3)
    for i in range(5):
        rec = DreamingRunRecord(input_msg_count=i)
        store.append(rec)

    rows = store.load()
    assert len(rows) == 3
    # 마지막 3개(2,3,4)만 남아 있어야 한다.
    assert [r.input_msg_count for r in rows] == [2, 3, 4]


def test_list_recent_returns_newest_first(tmp_path: Path):
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    for i in range(4):
        store.append(
            DreamingRunRecord(
                started_at=datetime(2026, 5, 1) + timedelta(days=i),
                input_msg_count=i,
                ended_at=datetime(2026, 5, 1) + timedelta(days=i, seconds=1),
            )
        )

    recent = store.list_recent(limit=2)
    assert len(recent) == 2
    # 최신부터: input_msg_count 3 → 2
    assert recent[0].input_msg_count == 3
    assert recent[1].input_msg_count == 2


def test_last_successful_run_skips_errors_and_skips(tmp_path: Path):
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    base = datetime(2026, 5, 1)
    # success → error → skip 순으로 적재. last_successful 은 첫 번째여야 한다.
    store.append(
        DreamingRunRecord(
            started_at=base, ended_at=base + timedelta(seconds=1),
            input_msg_count=3, generated_insight_count=1,
        )
    )
    store.append(
        DreamingRunRecord(
            started_at=base + timedelta(hours=1),
            ended_at=base + timedelta(hours=1, seconds=1),
            error="boom",
        )
    )
    store.append(
        DreamingRunRecord(
            started_at=base + timedelta(hours=2),
            ended_at=base + timedelta(hours=2, seconds=1),
            skip_reason=SKIP_NO_MESSAGES,
        )
    )

    last_succ = store.last_successful_run()
    assert last_succ is not None
    assert last_succ.input_msg_count == 3


def test_kpi_window_aggregates_by_status(tmp_path: Path):
    """7일 윈도우 KPI — success/skip/error 카운트와 skip 사유 breakdown 이 맞는지."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    now = datetime(2026, 5, 4, 12, 0, 0)
    # 윈도우 안 (최근 7일 내)
    store.append(
        DreamingRunRecord(
            started_at=now - timedelta(days=1),
            ended_at=now - timedelta(days=1, seconds=-2),
            input_msg_count=10, generated_insight_count=2, rejected_count=1,
        )
    )
    store.append(
        DreamingRunRecord(
            started_at=now - timedelta(days=2),
            ended_at=now - timedelta(days=2, seconds=-1),
            skip_reason=SKIP_NO_MESSAGES,
        )
    )
    store.append(
        DreamingRunRecord(
            started_at=now - timedelta(days=3),
            ended_at=now - timedelta(days=3, seconds=-1),
            skip_reason=SKIP_EMPTY_RESULTS,
        )
    )
    store.append(
        DreamingRunRecord(
            started_at=now - timedelta(days=4),
            ended_at=now - timedelta(days=4, seconds=-1),
            error="LLM 5xx",
        )
    )
    # 윈도우 밖 (8일 전) — 집계에서 빠져야 한다.
    store.append(
        DreamingRunRecord(
            started_at=now - timedelta(days=8),
            ended_at=now - timedelta(days=8, seconds=-1),
            input_msg_count=999,
        )
    )

    kpi = store.kpi_window(days=7, now=now)
    assert kpi["total_runs"] == 4
    assert kpi["success"] == 1
    assert kpi["skip"] == 2
    assert kpi["error"] == 1
    assert kpi["input_msg_total"] == 10  # 윈도우 밖 999 는 제외
    assert kpi["insight_total"] == 2
    assert kpi["rejected_total"] == 1
    assert kpi["skip_breakdown"] == {SKIP_NO_MESSAGES: 1, SKIP_EMPTY_RESULTS: 1}


def test_kpi_window_with_no_runs(tmp_path: Path):
    """행이 한 건도 없을 때 KPI 가 모두 0 인지."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    kpi = store.kpi_window(days=7)
    assert kpi["total_runs"] == 0
    assert kpi["success"] == 0
    assert kpi["skip_breakdown"] == {}


def test_load_skips_malformed_lines(tmp_path: Path, caplog):
    """JSONL 한 줄이 깨져도 나머지 행은 정상 로드되어야 한다."""
    import logging

    p = tmp_path / "runs.jsonl"
    valid = DreamingRunRecord(
        started_at=datetime(2026, 5, 4),
        ended_at=datetime(2026, 5, 4, 0, 0, 1),
        input_msg_count=1,
    )
    p.write_text(
        "{not json}\n"
        + json.dumps(valid.to_dict(), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    store = DreamingRunStore(p)
    with caplog.at_level(logging.WARNING):
        rows = store.load()
    assert len(rows) == 1
    assert rows[0].input_msg_count == 1


def test_atomic_write_no_tmp_file_left_behind(tmp_path: Path):
    """save_all 후 .tmp 파일이 남아 있지 않아야 한다."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    store.append(DreamingRunRecord(input_msg_count=1))
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []


def test_max_records_invalid_falls_back_to_default(tmp_path: Path):
    """0/음수 max_records 는 기본값으로 폴백."""
    store = DreamingRunStore(tmp_path / "runs.jsonl", max_records=0)
    assert store._max_records == DreamingRunStore.DEFAULT_MAX_RECORDS


def test_finish_merges_details_with_existing(tmp_path: Path):
    """begin 시점 details 와 finish 시점 details 가 병합되어야 한다."""
    store = DreamingRunStore(tmp_path / "runs.jsonl")
    rec = store.begin()
    rec.details = {"begin_note": "started"}
    store.update(rec)
    store.finish(rec, details={"end_note": "done"})

    last = store.last_run()
    assert last is not None
    assert last.details == {"begin_note": "started", "end_note": "done"}
