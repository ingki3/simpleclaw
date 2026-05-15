"""BIZ-202 — 레시피 디렉터리를 운영 디렉터리(`~/.simpleclaw/recipes/`)로 이전.

배경: 봇이 채팅에서 만든 레시피는 봇 워크스페이스(`~/.simpleclaw/workspace`) 의
상대 CWD 기준으로 작성되어 `~/.simpleclaw/workspace/.agent/recipes/` 에 떨어지고,
데몬은 working tree CWD 기준 `.agent/recipes/` 를 봐서 정작 등록이 되지 않는다.
config 한 곳(`recipes.dir`)에서 절대 경로를 결정하도록 봉합(BIZ-202)한 뒤, 이미
working tree 의 `.agent/recipes/` 에 있던 레시피들을 새 위치로 한 번 옮긴다.

설계 결정:
- 기본은 **복사**(원본 보존) — git 히스토리/리뷰 흔적을 그대로 두고 다음 마이너
  릴리스의 cleanup 패스에서 working tree 잔여물을 제거한다.
- ``--symlink`` 옵션은 디스크 공간이나 동기화 요구가 있는 운영자만 선택.
- target 에 이미 같은 이름이 있으면 *skip* (사용자가 손으로 갱신했을 수 있음).
  ``--force`` 로만 덮어쓴다.
- ``--dry-run`` 이 기본 — 실제 이동은 ``--apply`` 로 명시.

Usage:
    .venv/bin/python scripts/migrate_recipes_dir.py            # dry-run 미리보기
    .venv/bin/python scripts/migrate_recipes_dir.py --apply    # 실제 복사
    .venv/bin/python scripts/migrate_recipes_dir.py --apply --symlink
    .venv/bin/python scripts/migrate_recipes_dir.py --apply --force
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from simpleclaw.config import load_recipes_config

logger = logging.getLogger("migrate_recipes_dir")


def _migrate_one(
    name: str,
    src: Path,
    dst: Path,
    *,
    apply: bool,
    symlink: bool,
    force: bool,
) -> str:
    """단일 레시피 디렉터리를 이전한다.

    Returns:
        결과 라벨: ``"copied"``, ``"symlinked"``, ``"skipped:exists"``, ``"dry-run"``.
    """
    if dst.exists() and not force:
        return "skipped:exists"

    if not apply:
        return "dry-run"

    if dst.exists():
        # --force 일 때만 도달
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        dst.symlink_to(src.resolve())
        return "symlinked"
    shutil.copytree(src, dst)
    return "copied"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", default="config.yaml",
        help="config.yaml 경로 (기본: ./config.yaml)",
    )
    parser.add_argument(
        "--legacy", default=".agent/recipes",
        help="레거시 레시피 디렉터리 (기본: .agent/recipes)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="실제 이전 수행. 기본은 dry-run 미리보기.",
    )
    parser.add_argument(
        "--symlink", action="store_true",
        help="복사 대신 심볼릭 링크 생성.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="target 에 이미 같은 이름이 있어도 덮어씀.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    legacy_dir = Path(args.legacy).expanduser()
    target_dir = Path(load_recipes_config(args.config)["dir"]).expanduser()

    print(f"Legacy dir: {legacy_dir}")
    print(f"Target dir: {target_dir}")
    print(f"Mode      : {'apply' if args.apply else 'DRY-RUN'}"
          f"{' (symlink)' if args.symlink else ''}"
          f"{' (force)' if args.force else ''}")
    print()

    if not legacy_dir.is_dir():
        print(f"  → legacy dir '{legacy_dir}' missing — nothing to migrate.")
        # 그래도 target 디렉터리는 만들어둔다 (봇이 첫 부팅에서 쓰기 가능하도록).
        if args.apply:
            target_dir.mkdir(parents=True, exist_ok=True)
        return 0

    entries = sorted(
        e for e in legacy_dir.iterdir()
        if e.is_dir() and not e.name.startswith(".")
    )
    if not entries:
        print(f"  → legacy dir '{legacy_dir}' empty — nothing to migrate.")
        if args.apply:
            target_dir.mkdir(parents=True, exist_ok=True)
        return 0

    if args.apply:
        target_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, str] = {}
    for entry in entries:
        name = entry.name
        dst = target_dir / name
        try:
            results[name] = _migrate_one(
                name, entry, dst,
                apply=args.apply, symlink=args.symlink, force=args.force,
            )
        except OSError as e:
            results[name] = f"error:{e}"

    width = max(len(n) for n in results)
    for name, status in results.items():
        print(f"  - {name.ljust(width)}  {status}")

    print()
    if not args.apply:
        print("(dry-run) Re-run with --apply to perform the migration.")
    else:
        copied = sum(1 for v in results.values() if v in ("copied", "symlinked"))
        print(f"Done. {copied}/{len(results)} recipe(s) migrated.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
