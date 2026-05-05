"""BIZ-138 — `.agent/` 잔여 라이브 파일을 ``~/.simpleclaw/`` 격리 디렉터리로 정리.

배경
----
BIZ-133 으로 운영 디렉터리가 ``~/.simpleclaw/`` 로 이전됐지만, ``run_bot.py``
의 SafetyBackupManager wiring 만 ``.agent/...`` 하드코드가 남아 있어서 일부
환경에서는 봇이 다음 위치에 라이브 데이터를 계속 써버렸다 (BIZ-138):

- ``.agent/conversations.db`` (+ ``-wal``/``-shm``)
- ``.agent/daemon.db`` (+ ``-wal``/``-shm``)
- ``.agent/_safety_backup/{ts}/``
- ``.agent/{file}.backup-NNNN-YYYYMMDDTHHMMSS`` (마이그레이션 사이드카)

BIZ-138 의 코드 수정으로 봇은 이제 ``~/.simpleclaw/`` 만 사용하지만, 위
잔여물은 working tree 안에 남아 git 추적/실수 커밋의 위험이 된다. 본 스크립트는
그 잔여물을 운영 디렉터리 외부의 격리 폴더로 옮겨 보관한다 — 삭제하지 않으므로
복구가 가능하고, 운영자가 향후 데이터 비교를 마친 후 직접 정리하면 된다.

사용법
------
    .venv/bin/python scripts/cleanup_legacy_agent_dir.py            # dry-run (기본)
    .venv/bin/python scripts/cleanup_legacy_agent_dir.py --apply    # 실제 격리 이동

격리 위치
---------
``~/.simpleclaw/_quarantine_biz138_<YYYYMMDD_HHMMSS>/`` — 같은 마운트 안이라
``shutil.move`` 가 atomic rename 으로 떨어진다. 격리 후 working tree 의
``.agent/`` 에는 ``skills/`` ``recipes/`` 같은 *프로젝트 자산만* 남는다.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("cleanup_legacy_agent_dir")

# 격리 대상 파일명 — 운영 봇이 ``.agent/`` 에 만들 수 있는 라이브 데이터.
# ``skills/`` ``recipes/`` 같은 프로젝트 자산은 제외 (저장소 동봉이 의도).
_LIVE_FILES: tuple[str, ...] = (
    "conversations.db",
    "conversations.db-wal",
    "conversations.db-shm",
    "daemon.db",
    "daemon.db-wal",
    "daemon.db-shm",
    "HEARTBEAT.md",
    "daemon.pid",
    "AGENT.md",
    "USER.md",
    "MEMORY.md",
    "SOUL.md",
    "insights.jsonl",
    "suggestions.jsonl",
    "insight_blocklist.jsonl",
    "rejects.jsonl",
    "active_projects.jsonl",
    "dreaming_runs.jsonl",
    "bot.log",
)

_LIVE_DIRS: tuple[str, ...] = (
    "_safety_backup",
    "memory-backup",
    "workspace",
)

# 마이그레이션 사이드카: ``conversations.db.backup-0001-20260505T182242`` 같은 패턴.
# basename 의 ``\.backup-\d{4}-\d{8}T\d{6}`` 접미사로 식별한다.
_BACKUP_SIDECAR_PATTERN = re.compile(r"\.backup-\d{4}-\d{8}T\d{6}(-wal|-shm)?$")


def _iter_quarantine_targets(source_dir: Path):
    """source_dir 안에서 격리 대상 파일/디렉터리를 yield."""
    if not source_dir.is_dir():
        return

    for name in _LIVE_FILES:
        path = source_dir / name
        if path.exists():
            yield path

    for name in _LIVE_DIRS:
        path = source_dir / name
        if path.exists():
            yield path

    # 마이그레이션 사이드카 (``*.backup-NNNN-YYYYMMDDTHHMMSS``) 도 함께 격리.
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        if _BACKUP_SIDECAR_PATTERN.search(path.name):
            yield path


def _format_size(path: Path) -> str:
    """사람이 읽을 수 있는 크기 문자열을 만들어 dry-run 출력을 친근하게."""
    if path.is_dir():
        total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        return f"{total/1024:.1f}K (dir)"
    return f"{path.stat().st_size/1024:.1f}K"


def quarantine(
    source_dir: Path,
    quarantine_root: Path,
    *,
    apply: bool,
) -> dict[str, int]:
    """``source_dir`` 의 잔여 라이브 데이터를 ``quarantine_root`` 로 격리 이동.

    Returns:
        ``{"moved": N, "skipped": K}``.
    """
    moved = 0
    skipped = 0

    targets = list(_iter_quarantine_targets(source_dir))
    if not targets:
        logger.info("source 에 격리 대상 항목이 없습니다: %s", source_dir)
        return {"moved": 0, "skipped": 0}

    if apply:
        quarantine_root.mkdir(parents=True, exist_ok=True)

    for src in targets:
        rel = src.relative_to(source_dir)
        dest = quarantine_root / rel

        try:
            mtime = datetime.fromtimestamp(src.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            size = _format_size(src)
        except OSError:
            mtime = "?"
            size = "?"

        if not apply:
            logger.info(
                "[dry-run] 격리 예정: %s  (%s, mtime=%s) → %s",
                src.as_posix(), size, mtime, dest.as_posix(),
            )
            moved += 1
            continue

        if dest.exists():
            # 동일 격리 폴더에 같은 이름이 있으면 timestamp 접미사로 충돌 회피.
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = dest.with_name(f"{dest.name}.dup_{stamp}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dest))
        except OSError as exc:
            logger.warning("이동 실패 (%s → %s): %s", src, dest, exc)
            skipped += 1
            continue

        logger.info(
            "격리 완료: %s  (%s, mtime=%s) → %s",
            src.as_posix(), size, mtime, dest.as_posix(),
        )
        moved += 1

    return {"moved": moved, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(".agent"),
        help="잔여 정리 대상 디렉터리 (기본: .agent — working tree 내부)",
    )
    parser.add_argument(
        "--quarantine-root",
        type=Path,
        default=None,
        help=(
            "격리 디렉터리 (기본: ~/.simpleclaw/_quarantine_biz138_<ts>). "
            "지정 시 ts 접미사 없이 그대로 사용."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 이동 수행 (기본은 dry-run — 무엇이 옮겨질지만 표시)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG 로그",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    source_dir = args.source.expanduser()
    if args.quarantine_root is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine_root = (
            Path("~/.simpleclaw").expanduser() / f"_quarantine_biz138_{stamp}"
        )
    else:
        quarantine_root = args.quarantine_root.expanduser()

    logger.info("source: %s", source_dir)
    logger.info("quarantine root: %s", quarantine_root)
    if not args.apply:
        logger.info("(dry-run — 파일을 실제로 옮기지 않습니다. --apply 로 실행하세요.)")

    counters = quarantine(
        source_dir, quarantine_root, apply=args.apply,
    )

    logger.info(
        "done: moved=%d skipped=%d",
        counters["moved"], counters["skipped"],
    )

    if counters["moved"] == 0:
        logger.info(
            "잔여 항목이 없어 정리할 게 없습니다 — 이미 깨끗하거나 새 환경입니다.",
        )
    elif not args.apply:
        logger.info(
            "위 목록을 검토하고 ``--apply`` 를 추가해 다시 실행하면 격리가 수행됩니다.",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
