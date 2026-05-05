"""BIZ-133 — `.agent/` (저장소 안) → `~/.simpleclaw/` (저장소 외부) 이전 스크립트.

배경
----
BIZ-28(2026-05-05) 사고는 `git rm --cached` 로 untrack 된 `.agent/{AGENT,USER,
MEMORY,SOUL}.md` 가 머지·체크아웃 시퀀스 도중 dreaming 의 동시 쓰기와 race 가
나면서 working tree 에서 사라진 사고였다. BIZ-132(Phase 1+2) 의 safety_backup
+ preflight 자가 복원으로 *데이터 손실* 측면은 방어됐지만, 근본 원인인 "운영
데이터가 저장소 working tree 안에 살아 있는 구조" 자체는 변하지 않았다.

이 스크립트는 기존 운영 환경에서 1회 실행하여 라이브 데이터를 모두
`~/.simpleclaw/` 로 옮긴다. 실행 후에는 git 작업과 dreaming 런타임 쓰기가
물리적으로 다른 디렉터리를 보게 되어 race window 자체가 사라진다.

실행 후 동작
------------
- ``~/.simpleclaw/`` 에 라이브 파일이 위치하고 dreaming/agent 가 그 위치를 사용.
- 저장소의 ``.agent/`` 에는 더 이상 라이브 파일이 없다 — ``skills/`` / ``recipes/``
  같은 프로젝트 자산은 유지된다(저장소 동봉이 의도이므로 이전 대상 아님).

사용법
------
    .venv/bin/python scripts/migrate_local_dir.py            # 실제 이동
    .venv/bin/python scripts/migrate_local_dir.py --dry-run  # 이동 시뮬레이션만

이미 마이그레이션이 끝난 환경에서 다시 돌려도 안전하다 — 대상 위치에 같은
이름의 파일이 있으면 source 가 먼저 백업되고 노이즈 출력 없이 빠진다.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("migrate_local_dir")

# 이전 대상 — 운영 라이브 데이터만. skills/ recipes/ 같은 프로젝트 자산은 제외.
# 디렉터리 항목은 통째로 이동, 파일 항목은 단건 이동.
_LIVE_FILES: tuple[str, ...] = (
    # 페르소나 라이브 파일
    "AGENT.md",
    "USER.md",
    "MEMORY.md",
    "SOUL.md",
    # 인사이트/리뷰 sidecar
    "insights.jsonl",
    "suggestions.jsonl",
    "insight_blocklist.jsonl",
    "rejects.jsonl",
    # active-projects sidecar (BIZ-74)
    "active_projects.jsonl",
    # 드리밍 메트릭 sidecar (BIZ-81)
    "dreaming_runs.jsonl",
    # 데몬 상태/DB
    "HEARTBEAT.md",
    "daemon.pid",
    "daemon.db",
    "daemon.db-wal",
    "daemon.db-shm",
    "conversations.db",
    "conversations.db-wal",
    "conversations.db-shm",
    # 로그
    "bot.log",
)

_LIVE_DIRS: tuple[str, ...] = (
    # BIZ-132 신규 — dreaming 사이클 직전 라이브 파일을 통째로 스냅샷.
    "_safety_backup",
    # 레거시 dreaming 백업.
    "memory-backup",
    # 워크스페이스 (스킬 산출물 임시 디렉토리).
    "workspace",
)

# active_projects 는 jsonl 한 파일이지만 *.archive 같이 형제 파일이 추가될 수
# 있으므로 prefix 매칭으로 따라가는 편이 안전.
_LIVE_FILE_PREFIXES: tuple[str, ...] = (
    "active_projects",
)


def _make_unique_target(target: Path) -> Path:
    """대상 경로에 이미 파일이 있으면 ``.<timestamp>`` 접미사로 충돌 회피.

    덮어쓰기는 절대 하지 않는다 — 운영자가 두 사본을 비교하고 직접 정리하도록.
    """
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return target.with_name(f"{target.name}.preMigrate.{stamp}")


def _move_one(src: Path, dest_dir: Path, *, dry_run: bool) -> bool:
    """단일 파일/디렉터리를 ``dest_dir`` 로 이동한다.

    Returns:
        실제로 이동(또는 dry-run 시뮬레이션)했으면 True.
    """
    if not src.exists():
        return False

    target = _make_unique_target(dest_dir / src.name)
    rel_src = src.as_posix()
    rel_target = target.as_posix()

    if dry_run:
        logger.info("[dry-run] %s → %s", rel_src, rel_target)
        return True

    dest_dir.mkdir(parents=True, exist_ok=True)
    # shutil.move 는 파일/디렉터리 모두 처리하며, 같은 파일시스템에서는 rename 으로
    # 원자적 이동이 된다(BIZ-28 류 race 가 일어날 수 없는 상태로 곧장 진입).
    shutil.move(str(src), str(target))
    logger.info("moved: %s → %s", rel_src, rel_target)
    return True


def migrate(
    source_dir: Path,
    target_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """`.agent/` 라이브 파일·디렉터리를 ``~/.simpleclaw/`` 로 이전한다.

    Returns:
        ``{"files": N, "dirs": M, "skipped": K}`` — 운영자 사후 점검용 카운터.
    """
    moved_files = 0
    moved_dirs = 0
    skipped = 0

    if not source_dir.is_dir():
        logger.warning("source not found: %s — nothing to migrate", source_dir)
        return {"files": 0, "dirs": 0, "skipped": 0}

    # 1) 명시 파일 — 단건 이동.
    for name in _LIVE_FILES:
        src = source_dir / name
        if _move_one(src, target_dir, dry_run=dry_run):
            moved_files += 1

    # 2) prefix 매칭 — active_projects.* 같은 형제 파일을 따라가서 함께 이동.
    for prefix in _LIVE_FILE_PREFIXES:
        for src in sorted(source_dir.glob(f"{prefix}*")):
            if not src.is_file():
                continue
            # 이미 _LIVE_FILES 에서 처리된 정확 매치는 src.exists() 가 False 라
            # _move_one 이 자연스럽게 false 를 돌려주지만, 명시적으로 이중 카운트
            # 방지를 위해 여기서도 거른다.
            if src.name in _LIVE_FILES:
                continue
            if _move_one(src, target_dir, dry_run=dry_run):
                moved_files += 1

    # 3) 디렉터리 — 통째 이동.
    for name in _LIVE_DIRS:
        src = source_dir / name
        if _move_one(src, target_dir, dry_run=dry_run):
            moved_dirs += 1

    # 4) 남아 있는 항목 — skills/ recipes/ 같은 프로젝트 자산. 운영자에게 알려준다.
    if source_dir.is_dir():
        leftovers = sorted(p.name for p in source_dir.iterdir())
        if leftovers:
            logger.info(
                "remaining in %s (intentional, not migrated): %s",
                source_dir,
                ", ".join(leftovers),
            )
            skipped = len(leftovers)

    return {"files": moved_files, "dirs": moved_dirs, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 0 = 성공, 1 = source 누락(이동할 게 없음 — 새 환경)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(".agent"),
        help="이전할 원본 디렉터리 (기본: .agent — 저장소 working tree 내부)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("~/.simpleclaw").expanduser(),
        help=(
            "이전 대상 디렉터리 (기본: ~/.simpleclaw — config 의 새 기본값과 일치)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제로 옮기지 않고 어떤 파일이 어디로 갈지만 표시",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG 로그 활성화",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    source_dir = args.source.expanduser()
    target_dir = args.target.expanduser()

    logger.info("source: %s", source_dir)
    logger.info("target: %s", target_dir)
    if args.dry_run:
        logger.info("(dry-run — 파일을 실제로 옮기지 않습니다)")

    counters = migrate(source_dir, target_dir, dry_run=args.dry_run)

    logger.info(
        "done: files=%d dirs=%d skipped(in source)=%d",
        counters["files"], counters["dirs"], counters["skipped"],
    )

    if counters["files"] == 0 and counters["dirs"] == 0:
        logger.warning(
            "이전 대상이 없습니다. 이미 마이그레이션이 끝났거나 새 환경일 수 있습니다.",
        )
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
