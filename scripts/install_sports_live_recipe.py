"""Install the canonical ``sports-live`` production recipe."""

import argparse
from pathlib import Path

from simpleclaw.production_assets import (
    CANONICAL_SPORTS_LIVE_RECIPE as CANONICAL_RECIPE,
)
from simpleclaw.production_assets import (
    SPORTS_LIVE_RECIPE_NAME as RECIPE_NAME,
)
from simpleclaw.production_assets import install_sports_live_recipe as install

__all__ = ("CANONICAL_RECIPE", "RECIPE_NAME", "install")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes-dir", type=Path)
    args = parser.parse_args(argv)
    path = install(args.recipes_dir) if args.recipes_dir is not None else install()
    print(f"installed {RECIPE_NAME} at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
