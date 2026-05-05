"""BIZ-132 Phase 1 — SafetyBackupManager 단위 테스트.

검증 시나리오:
1. ``snapshot`` 이 위험 파일 목록을 timestamped 디렉터리에 통째로 복사한다.
2. 보존 정책 — ``max_cycles`` 초과 시 가장 오래된 사이클이 정리된다.
3. SQLite DB 는 ``Connection.backup`` API 로 atomic 복사된다(WAL 활성에서도 일관).
4. ``latest_backup_for`` 가 가장 최근 사이클의 동일 basename 을 우선 반환한다.
5. ``find_legacy_memory_backup`` 이 레거시 ``{stem}.{ts}.bak`` 중 mtime 최신본을 찾는다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from simpleclaw.memory.safety_backup import (
    SafetyBackupManager,
    find_legacy_memory_backup,
)


def _make_clock(times):
    """주어진 datetime 시퀀스를 순서대로 반환하는 가짜 시계."""
    iterator = iter(times)
    return lambda: next(iterator)


def _make_test_db(path: Path, rows: list[tuple[int, str]]) -> None:
    """테스트용 sqlite DB 를 생성하고 행을 채운다."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, label TEXT)")
        conn.executemany("INSERT INTO t(id, label) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _read_db_rows(path: Path) -> list[tuple[int, str]]:
    conn = sqlite3.connect(str(path))
    try:
        return list(conn.execute("SELECT id, label FROM t ORDER BY id").fetchall())
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 1. snapshot — 디렉터리 생성 및 파일 복사
# ----------------------------------------------------------------------


def test_snapshot_creates_timestamped_directory_with_all_files(tmp_path):
    """snapshot 이 timestamp 디렉터리를 만들고 plain 파일을 그대로 복사."""
    live_dir = tmp_path / "agent"
    live_dir.mkdir()
    f1 = live_dir / "MEMORY.md"
    f2 = live_dir / "USER.md"
    f1.write_text("memory body", encoding="utf-8")
    f2.write_text("user body", encoding="utf-8")

    backup_root = tmp_path / "_safety_backup"
    mgr = SafetyBackupManager(
        backup_root=backup_root,
        files=[f1, f2],
        clock=_make_clock([datetime(2026, 5, 5, 12, 0, 0)]),
    )
    target = mgr.snapshot()
    assert target is not None
    assert target.is_dir()
    assert target.name == "20260505_120000"
    assert (target / "MEMORY.md").read_text(encoding="utf-8") == "memory body"
    assert (target / "USER.md").read_text(encoding="utf-8") == "user body"


def test_snapshot_skips_missing_files_silently(tmp_path):
    """일부 sidecar 파일이 아직 생성되지 않았어도 백업은 성공한다."""
    live_dir = tmp_path / "agent"
    live_dir.mkdir()
    f1 = live_dir / "MEMORY.md"
    f1.write_text("hello", encoding="utf-8")
    missing = live_dir / "insights.jsonl"

    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[f1, missing],
        clock=_make_clock([datetime(2026, 5, 5, 12, 0, 0)]),
    )
    target = mgr.snapshot()
    assert target is not None
    assert (target / "MEMORY.md").is_file()
    # 누락 파일은 백업되지 않음 (파일 자체가 아예 만들어지지 않아야 한다 — 빈 파일 X)
    assert not (target / "insights.jsonl").exists()


def test_snapshot_returns_none_when_nothing_to_backup(tmp_path):
    """라이브 파일이 모두 부재면 빈 디렉터리 보관 X, None 반환."""
    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[tmp_path / "missing.md"],
        clock=_make_clock([datetime(2026, 5, 5, 12, 0, 0)]),
    )
    result = mgr.snapshot()
    assert result is None
    # 백업 루트에 사이클 디렉터리가 만들어지지 않았어야 한다.
    if (tmp_path / "bak").exists():
        assert not any((tmp_path / "bak").iterdir())


def test_snapshot_handles_same_second_invocations(tmp_path):
    """같은 초에 두 번 호출해도 디렉터리가 충돌하지 않는다(suffix 부여)."""
    live = tmp_path / "f.md"
    live.write_text("a", encoding="utf-8")

    fixed = datetime(2026, 5, 5, 12, 0, 0)
    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[live],
        clock=_make_clock([fixed, fixed]),
    )
    t1 = mgr.snapshot()
    t2 = mgr.snapshot()
    assert t1 is not None and t2 is not None
    assert t1 != t2
    assert (t1 / "f.md").is_file()
    assert (t2 / "f.md").is_file()


# ----------------------------------------------------------------------
# 2. 보존 정책 — max_cycles 초과 시 회전
# ----------------------------------------------------------------------


def test_prune_keeps_only_max_cycles_most_recent(tmp_path):
    """max_cycles=3 인 매니저에 5번 snapshot → 가장 최근 3개만 남는다."""
    live = tmp_path / "f.md"
    live.write_text("x", encoding="utf-8")

    times = [datetime(2026, 5, 5, 12, 0, i) for i in range(5)]
    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[live],
        max_cycles=3,
        clock=_make_clock(times),
    )
    for _ in range(5):
        mgr.snapshot()

    cycles = sorted(p.name for p in (tmp_path / "bak").iterdir() if p.is_dir())
    assert len(cycles) == 3
    # 가장 최근 3개 — 12:00:02, 12:00:03, 12:00:04
    assert cycles == ["20260505_120002", "20260505_120003", "20260505_120004"]


def test_max_cycles_below_one_coerced_to_one(tmp_path):
    """max_cycles=0 같은 오설정은 1로 강제 — '가장 최근 1개는 항상 보존' 보장."""
    live = tmp_path / "f.md"
    live.write_text("x", encoding="utf-8")

    times = [datetime(2026, 5, 5, 12, 0, i) for i in range(3)]
    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[live],
        max_cycles=0,
        clock=_make_clock(times),
    )
    for _ in range(3):
        mgr.snapshot()
    cycles = sorted((tmp_path / "bak").iterdir(), key=lambda p: p.name)
    assert len(cycles) == 1
    assert cycles[0].name == "20260505_120002"


# ----------------------------------------------------------------------
# 3. SQLite DB atomic 복사
# ----------------------------------------------------------------------


def test_database_copied_via_sqlite_backup_api(tmp_path):
    """DB 는 ``Connection.backup`` 으로 복사돼 행 데이터 그대로 보존."""
    db = tmp_path / "conv.db"
    rows = [(1, "hello"), (2, "world")]
    _make_test_db(db, rows)

    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        databases=[db],
        clock=_make_clock([datetime(2026, 5, 5, 12, 0, 0)]),
    )
    target = mgr.snapshot()
    assert target is not None
    backed_up = target / "conv.db"
    assert backed_up.is_file()
    assert _read_db_rows(backed_up) == rows


def test_database_backup_isolated_from_live_writes(tmp_path):
    """백업 직후 라이브 DB 에 새 행을 써도 백업 사본에는 반영되지 않는다(스냅샷 일관성)."""
    db = tmp_path / "conv.db"
    _make_test_db(db, [(1, "before")])

    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        databases=[db],
        clock=_make_clock([datetime(2026, 5, 5, 12, 0, 0)]),
    )
    target = mgr.snapshot()

    # 라이브 DB 에 새 행 추가
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("INSERT INTO t(id, label) VALUES (2, 'after')")
        conn.commit()
    finally:
        conn.close()

    backed_up_rows = _read_db_rows(target / "conv.db")
    assert backed_up_rows == [(1, "before")]
    live_rows = _read_db_rows(db)
    assert live_rows == [(1, "before"), (2, "after")]


# ----------------------------------------------------------------------
# 4. latest_backup_for — Phase 2 자가 복원의 입력
# ----------------------------------------------------------------------


def test_latest_backup_for_returns_most_recent_cycle(tmp_path):
    """여러 사이클이 쌓여 있을 때 ``latest_backup_for`` 는 가장 최근 사본을 반환."""
    live = tmp_path / "MEMORY.md"

    times = [datetime(2026, 5, 5, 12, 0, i) for i in range(3)]
    mgr = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[live],
        clock=_make_clock(times),
    )
    # 사이클마다 본문을 다르게 만들어 어느 시점이 반환되는지 확인.
    for i in range(3):
        live.write_text(f"version-{i}", encoding="utf-8")
        mgr.snapshot()

    found = mgr.latest_backup_for("MEMORY.md")
    assert found is not None
    assert found.read_text(encoding="utf-8") == "version-2"


def test_latest_backup_for_returns_none_when_no_match(tmp_path):
    mgr = SafetyBackupManager(backup_root=tmp_path / "bak")
    assert mgr.latest_backup_for("MEMORY.md") is None


def test_latest_backup_for_skips_cycles_without_match(tmp_path):
    """오래된 사이클에는 있지만 새 사이클에는 없을 때, 가장 최근 *매치* 를 반환."""
    live_a = tmp_path / "A.md"
    live_b = tmp_path / "B.md"
    live_a.write_text("aaa", encoding="utf-8")
    live_b.write_text("bbb", encoding="utf-8")

    times = [datetime(2026, 5, 5, 12, 0, i) for i in range(2)]
    # 첫 사이클은 A, B 둘 다 백업. 두 번째 사이클은 A 만 백업(B 라이브 부재 시뮬레이션).
    mgr1 = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[live_a, live_b],
        clock=_make_clock([times[0]]),
    )
    mgr1.snapshot()

    live_b.unlink()
    mgr2 = SafetyBackupManager(
        backup_root=tmp_path / "bak",
        files=[live_a, live_b],
        clock=_make_clock([times[1]]),
    )
    mgr2.snapshot()

    found = mgr2.latest_backup_for("B.md")
    assert found is not None
    # 가장 최근 매치는 첫 사이클(B 가 거기 있었음).
    assert found.parent.name == "20260505_120000"


# ----------------------------------------------------------------------
# 5. find_legacy_memory_backup — 레거시 .bak 폴백
# ----------------------------------------------------------------------


def test_find_legacy_memory_backup_returns_most_recent(tmp_path):
    backup_dir = tmp_path / "memory-backup"
    backup_dir.mkdir()
    older = backup_dir / "MEMORY.20260101_000000.bak"
    newer = backup_dir / "MEMORY.20260505_120000.bak"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    # mtime 보정 — 정렬 기준이 mtime 임을 명확히.
    import os
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    found = find_legacy_memory_backup(backup_dir, "MEMORY.md")
    assert found == newer


def test_find_legacy_memory_backup_none_when_dir_missing(tmp_path):
    assert find_legacy_memory_backup(tmp_path / "nope", "MEMORY.md") is None


def test_find_legacy_memory_backup_none_when_no_match(tmp_path):
    backup_dir = tmp_path / "memory-backup"
    backup_dir.mkdir()
    (backup_dir / "USER.20260101_000000.bak").write_text("u")
    assert find_legacy_memory_backup(backup_dir, "MEMORY.md") is None
