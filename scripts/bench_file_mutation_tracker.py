"""BIZ-251 — FileMutationTracker latency bench.

DoD: 워크스페이스가 1000+ 파일일 때 턴당 추가 latency < 50ms.

실측: 다양한 (file_count, change_count) 시나리오에서 ``snapshot(previous=...)
+ diff() + format_footer()`` 의 합산 시간을 측정한다. ``snapshot()`` 자체는
처음 1회만 무겁고, 두 번째부터는 cached line_count 재사용으로 stat 비용만
지불한다 — 두 번째 측정값이 운영 환경의 정상 비용이다.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from pathlib import Path

from simpleclaw.agent.file_mutation_tracker import (
    FileMutationTracker,
    TrackedRoot,
    format_footer,
)


def _seed_workspace(root: Path, file_count: int, lines_per_file: int) -> None:
    """``file_count`` 개의 파일을 ``root`` 아래 64 파일/dir 로 분포시켜 생성."""
    files_per_dir = 64
    body = "".join(f"line {i}\n" for i in range(lines_per_file))
    written = 0
    dir_idx = 0
    while written < file_count:
        subdir = root / f"dir_{dir_idx:04d}"
        subdir.mkdir(parents=True, exist_ok=True)
        for j in range(files_per_dir):
            if written >= file_count:
                break
            (subdir / f"file_{j:03d}.txt").write_text(body)
            written += 1
        dir_idx += 1


def _apply_changes(root: Path, n_added: int, n_modified: int, n_deleted: int) -> None:
    """워크스페이스에 ``n_added`` 신규, ``n_modified`` 수정, ``n_deleted`` 삭제."""
    new_dir = root / "_new"
    new_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n_added):
        (new_dir / f"add_{i:04d}.txt").write_text(f"new content {i}\n")

    # 수정: 가장 첫 디렉터리의 파일들 일부 갱신
    first_dir = next(p for p in sorted(root.iterdir()) if p.is_dir() and p.name != "_new")
    targets = sorted(first_dir.iterdir())[:n_modified]
    for p in targets:
        with open(p, "a") as fh:
            fh.write("appended\n")

    # 삭제: 두 번째 디렉터리에서
    second_dir = min(p for p in root.iterdir() if p.is_dir() and p.name != "_new" and p != first_dir)
    targets = sorted(second_dir.iterdir())[:n_deleted]
    for p in targets:
        p.unlink()


def run_scenario(
    file_count: int,
    n_added: int = 0,
    n_modified: int = 0,
    n_deleted: int = 0,
    *,
    repeats: int = 5,
) -> dict:
    tmpdir = Path(tempfile.mkdtemp(prefix="fmt-bench-"))
    workspace = tmpdir / "workspace"
    persona = tmpdir / "persona"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent\n")
    (persona / "MEMORY.md").write_text("# Memory\n")

    try:
        _seed_workspace(workspace, file_count, lines_per_file=20)

        tracker = FileMutationTracker(
            [
                TrackedRoot(".agent/workspace", workspace),
                TrackedRoot(".agent", persona,
                            files=("AGENT.md", "MEMORY.md")),
            ]
        )

        # warm-up — 첫 snapshot 은 모든 파일의 line_count 를 처음 계산하므로
        # 비용이 다르다. 운영 환경에서는 부팅 직후 1회 발생.
        t0 = time.perf_counter()
        prev = tracker.snapshot()
        warm_ms = (time.perf_counter() - t0) * 1000

        if n_added or n_modified or n_deleted:
            _apply_changes(workspace, n_added, n_modified, n_deleted)

        # 본 측정: snapshot(previous=prev) + diff + format_footer
        samples = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            curr = tracker.snapshot(previous=prev)
            diff = tracker.diff(prev, curr)
            _ = format_footer(diff)
            samples.append((time.perf_counter() - t0) * 1000)

        return {
            "file_count": file_count,
            "added": n_added,
            "modified": n_modified,
            "deleted": n_deleted,
            "warmup_ms": warm_ms,
            "samples_ms": samples,
            "median_ms": statistics.median(samples),
            "max_ms": max(samples),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeats", type=int, default=5,
        help="시나리오별 반복 측정 횟수 (median 산출)",
    )
    args = parser.parse_args()

    scenarios = [
        # (label, file_count, n_added, n_modified, n_deleted)
        ("baseline-1000-noop", 1024, 0, 0, 0),
        ("baseline-2000-noop", 2048, 0, 0, 0),
        ("1000-small-change", 1024, 1, 1, 1),
        ("1000-burst-change", 1024, 10, 10, 0),
        ("5000-noop", 5120, 0, 0, 0),
    ]

    print(
        f"{'scenario':<25}{'files':>8}{'+A':>5}{'M':>5}{'-D':>5}"
        f"{'warmup':>10}{'median':>10}{'max':>10}"
    )
    print("-" * 78)
    any_fail = False
    for label, fc, a, m, d in scenarios:
        r = run_scenario(fc, a, m, d, repeats=args.repeats)
        warn = " ⚠" if r["median_ms"] >= 50 else ""
        if r["median_ms"] >= 50 and fc <= 2048:
            any_fail = True
        print(
            f"{label:<25}{r['file_count']:>8}{r['added']:>5}{r['modified']:>5}"
            f"{r['deleted']:>5}{r['warmup_ms']:>10.1f}{r['median_ms']:>10.1f}"
            f"{r['max_ms']:>10.1f}{warn}"
        )

    print()
    print(
        "DoD: 1024-file 시나리오에서 median 추가 latency < 50ms "
        "(워크스페이스 1000+ 파일 기준)."
    )
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
