"""Install the canonical Naver Sports runtime skill wrapper."""

import argparse
from pathlib import Path

from simpleclaw.production_assets import (
    CANONICAL_NAVER_SPORTS_SKILL_MD as CANONICAL_SKILL_MD,
)
from simpleclaw.production_assets import (
    NAVER_SPORTS_SKILL_NAME as SKILL_NAME,
)
from simpleclaw.production_assets import install_naver_sports_skill as install

__all__ = ("CANONICAL_SKILL_MD", "SKILL_NAME", "install")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-dir", type=Path)
    args = parser.parse_args(argv)
    path = install(args.global_dir) if args.global_dir is not None else install()
    print(f"installed {SKILL_NAME} at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
