"""FileMutationTracker 단위 테스트 (BIZ-251).

스냅샷·diff·footer 포맷이 워크스페이스/페르소나 파일의 변경을 정확히
포착하고, 변경 없음 시 빈 footer 를 돌려줘 토큰을 절약하는지 검증.
"""

from __future__ import annotations

import os
import time

from simpleclaw.agent.file_mutation_tracker import (
    FileMutationTracker,
    TrackedRoot,
    format_footer,
)


def _bump_mtime(path) -> None:
    """파일 시스템 mtime 해상도 차이를 흡수하기 위해 0.01s 뒤로 밀어준다."""
    st = path.stat()
    new_mtime = st.st_mtime + 1.0
    os.utime(path, (new_mtime, new_mtime))


def _make_tracker(workspace_dir, persona_dir):
    return FileMutationTracker(
        [
            TrackedRoot(".agent/workspace", workspace_dir),
            TrackedRoot(
                ".agent", persona_dir,
                files=("AGENT.md", "USER.md", "MEMORY.md"),
            ),
        ]
    )


# ----------------------------------------------------------------------
# 기본 동작
# ----------------------------------------------------------------------


def test_empty_workspace_no_changes_returns_empty_footer(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()
    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    assert diff.is_empty
    assert format_footer(diff) == ""


def test_added_file_renders_with_line_count(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()

    (ws / "report.md").write_text("# Title\n\nbody line 1\nbody line 2\n")

    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.kind == "added"
    assert change.display_path == ".agent/workspace/report.md"
    assert change.new_lines == 4

    footer = format_footer(diff)
    assert footer.startswith("[file changes this turn]")
    assert "+ .agent/workspace/report.md (4 lines)" in footer


def test_modified_file_renders_old_and_new(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    (ws / "notes.md").write_text("a\nb\nc\n")

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()

    (ws / "notes.md").write_text("a\nb\nc\nd\ne\nf\n")
    _bump_mtime(ws / "notes.md")

    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    assert len(diff.changes) == 1
    change = diff.changes[0]
    assert change.kind == "modified"
    assert change.old_lines == 3
    assert change.new_lines == 6

    footer = format_footer(diff)
    assert "M .agent/workspace/notes.md (3 lines → 6 lines)" in footer


def test_deleted_file_renders(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    target = ws / "scratch.txt"
    target.write_text("one\ntwo\n")

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()

    target.unlink()

    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    assert len(diff.changes) == 1
    assert diff.changes[0].kind == "deleted"
    footer = format_footer(diff)
    assert "- .agent/workspace/scratch.txt (was 2 lines)" in footer


def test_persona_files_tracked_via_allowlist(tmp_path):
    """페르소나 root 는 명시한 파일만 추적되어야 한다 — dreaming 부산물
    (SQLite WAL, embedding cache) 가 노이즈로 footer 에 끼면 안 됨."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    (persona / "AGENT.md").write_text("# Agent\n")
    (persona / "MEMORY.md").write_text("# Memory\n- old fact\n")
    # 추적되면 안 되는 파일들
    (persona / "conversations.db").write_text("BINARY")
    (persona / "conversations.db-wal").write_text("WAL")
    (persona / "untracked_log.txt").write_text("noise")

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()

    # 페르소나 외 파일 / 화이트리스트 외 파일 모두 변경 — footer 에 안 나와야 함
    (persona / "conversations.db").write_text("BINARY2")
    (persona / "untracked_log.txt").write_text("noise2")
    _bump_mtime(persona / "conversations.db")
    _bump_mtime(persona / "untracked_log.txt")

    # 화이트리스트 파일은 수정 — 노출되어야 함
    (persona / "MEMORY.md").write_text("# Memory\n- old fact\n- new fact\n")
    _bump_mtime(persona / "MEMORY.md")

    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    paths = [c.display_path for c in diff.changes]
    assert paths == [".agent/MEMORY.md"], (
        f"화이트리스트 외 파일이 footer 에 새었음: {paths}"
    )


def test_workspace_walk_skips_noise_directories(tmp_path):
    """``.git``, ``__pycache__`` 등은 walk 가지치기로 추적되지 않아야 한다."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\x00\x01")
    (ws / "real.md").write_text("real\n")

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()

    # 노이즈 디렉터리 안 파일들도 수정
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/dev\n")
    _bump_mtime(ws / ".git" / "HEAD")
    (ws / "real.md").write_text("real\nupdated\n")
    _bump_mtime(ws / "real.md")

    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    paths = [c.display_path for c in diff.changes]
    assert paths == [".agent/workspace/real.md"], (
        f"노이즈 디렉터리가 walk 에 포함됨: {paths}"
    )


def test_workspace_walk_skips_sqlite_sidecars(tmp_path):
    """워크스페이스에 SQLite sidecar (``-wal``, ``-shm``) 가 생겨도 추적
    제외되어야 한다 — 도구가 의도적으로 쓴 결과가 아님."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    (ws / "log.db-wal").write_bytes(b"\x00" * 32)
    (ws / "log.db-shm").write_bytes(b"\x00" * 32)
    (ws / "report.md").write_text("hi\n")

    tracker = _make_tracker(ws, persona)
    snap = tracker.snapshot()
    paths = list(snap.entries.keys())
    assert ".agent/workspace/report.md" in paths
    assert all("-wal" not in p and "-shm" not in p for p in paths), (
        f"SQLite sidecar 가 추적됨: {paths}"
    )


def test_diff_order_added_modified_deleted(tmp_path):
    """결정론적 순서: + → M → -, 같은 종류 내 알파벳 오름차순.
    LLM 캐시 친화성과 footer 회귀 스냅샷 양쪽을 위해 안정 순서 보장."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    (ws / "z_modified.md").write_text("a\n")
    (ws / "y_deleted.md").write_text("b\n")

    tracker = _make_tracker(ws, persona)
    before = tracker.snapshot()

    (ws / "a_added.md").write_text("new\n")
    (ws / "b_added.md").write_text("new\n")
    (ws / "z_modified.md").write_text("a\nupdated\n")
    _bump_mtime(ws / "z_modified.md")
    (ws / "y_deleted.md").unlink()

    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)

    kinds = [c.kind for c in diff.changes]
    paths = [c.display_path for c in diff.changes]
    assert kinds == ["added", "added", "modified", "deleted"], kinds
    assert paths == [
        ".agent/workspace/a_added.md",
        ".agent/workspace/b_added.md",
        ".agent/workspace/z_modified.md",
        ".agent/workspace/y_deleted.md",
    ], paths


def test_line_count_reused_for_unchanged_files(tmp_path):
    """unchanged file 의 line_count 는 previous snapshot 에서 재사용되어
    매 턴 디스크 read 가 발생하지 않아야 한다 (large workspace 비용 가드)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    target = ws / "stable.md"
    target.write_text("a\nb\nc\n")

    tracker = _make_tracker(ws, persona)
    first = tracker.snapshot()

    # 두 번째 스냅샷: 파일 변경 없음. 캐시된 line_count 가 그대로 재사용
    # 되어야 하므로 entry 가 ``is`` 같지 않더라도 값은 일치해야 한다.
    second = tracker.snapshot(previous=first)
    key = ".agent/workspace/stable.md"
    assert second.entries[key].line_count == 3
    # 캐시 hit: 동일 객체가 그대로 들어와야 한다 (불필요 read 가 없었음).
    assert second.entries[key] is first.entries[key]


def test_binary_or_large_file_falls_back_to_bytes(tmp_path):
    """line_count_max_bytes 초과 파일은 footer 에 ``N bytes`` 로 표시."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    # 작은 한계값으로 분기 강제
    tracker = FileMutationTracker(
        [
            TrackedRoot(".agent/workspace", ws),
            TrackedRoot(".agent", persona, files=("AGENT.md",)),
        ],
        line_count_max_bytes=8,
    )

    before = tracker.snapshot()
    (ws / "blob.bin").write_bytes(b"\x00" * 64)
    after = tracker.snapshot(previous=before)
    diff = tracker.diff(before, after)
    footer = format_footer(diff)
    assert "(64 bytes)" in footer


def test_no_change_after_idempotent_snapshot(tmp_path):
    """두 번 연속 스냅샷 (사이 디스크 활동 없음) → 빈 diff."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()
    (ws / "a.md").write_text("a\n")

    tracker = _make_tracker(ws, persona)
    s1 = tracker.snapshot()
    s2 = tracker.snapshot(previous=s1)
    assert tracker.diff(s1, s2).is_empty


def test_missing_root_handled_gracefully(tmp_path):
    """존재하지 않는 root 경로는 walk 건너뛰고 빈 entries 반환."""
    tracker = FileMutationTracker(
        [TrackedRoot(".agent/workspace", tmp_path / "does-not-exist")]
    )
    snap = tracker.snapshot()
    assert snap.entries == {}


def test_footer_omitted_when_diff_empty(tmp_path):
    """변경 없음 → footer 빈 문자열 (토큰 절약 DoD)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()
    (ws / "stable.md").write_text("hi\n")

    tracker = _make_tracker(ws, persona)
    s1 = tracker.snapshot()
    s2 = tracker.snapshot(previous=s1)
    assert format_footer(tracker.diff(s1, s2)) == ""


# ----------------------------------------------------------------------
# 성능 가드
# ----------------------------------------------------------------------


def test_snapshot_latency_under_50ms_for_1000_files(tmp_path):
    """DoD: 워크스페이스 1000+ 파일에서 턴당 추가 latency < 50ms.

    실측 환경에 따라 흔들리므로 200ms 의 보수적 cap 으로 가드. 회귀 기준
    (워크 자체가 O(n²) 가 되거나 매 턴 read 가 누설되는 사고) 잡기 위함.
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()

    # 1000+ 파일 생성 (4 depth × 256 파일 + α)
    for i in range(64):
        subdir = ws / f"dir_{i:03d}"
        subdir.mkdir()
        for j in range(16):
            (subdir / f"file_{j:03d}.txt").write_text(f"line {i}-{j}\n" * 5)

    tracker = _make_tracker(ws, persona)

    # 1st snapshot: 모든 파일을 처음 카운트하므로 느리다 (warm-up).
    first = tracker.snapshot()
    file_count = len(first.entries)
    assert file_count >= 1000, f"테스트 픽스처가 1000 파일 미만: {file_count}"

    # 2nd snapshot: 변경 없음 → 캐시 재사용으로 stat 비용만 들어야 함.
    start = time.perf_counter()
    second = tracker.snapshot(previous=first)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert tracker.diff(first, second).is_empty
    assert elapsed_ms < 200, (
        f"FileMutationTracker.snapshot() 가 {file_count} 파일에서 "
        f"{elapsed_ms:.1f}ms 소요 — DoD 50ms 의 4x 가드 초과. 회귀 의심."
    )


def test_max_files_truncates_walk(tmp_path, caplog):
    """``max_files`` 초과 시 walk 가 truncate 되고 경고만 남는다 (서비스 보존)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    persona = tmp_path / "persona"
    persona.mkdir()
    for i in range(20):
        (ws / f"f_{i:03d}.txt").write_text(f"{i}\n")

    tracker = FileMutationTracker(
        [TrackedRoot(".agent/workspace", ws)],
        max_files=10,
    )
    snap = tracker.snapshot()
    assert len(snap.entries) == 10
