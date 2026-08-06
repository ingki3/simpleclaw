"""Install the canonical ``sports-live`` production recipe."""

import argparse
from pathlib import Path

from simpleclaw.production_assets import (
    CANONICAL_SPORTS_LIVE_RECIPE as CANONICAL_RECIPE,
)
from simpleclaw.production_assets import DEFAULT_CONFIG_PATH
from simpleclaw.production_assets import (
    SPORTS_LIVE_RECIPE_NAME as RECIPE_NAME,
)
from simpleclaw.production_assets import install_sports_live_recipe as install

__all__ = ("CANONICAL_RECIPE", "RECIPE_NAME", "install")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes-dir", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="recipes.dir를 읽을 config.yaml 경로",
    )
    args = parser.parse_args(argv)
    path = install(args.recipes_dir, config_path=args.config)
    destination_source = (
        "--recipes-dir"
        if args.recipes_dir is not None
        else f"config:{args.config.expanduser()}"
    )
    print(
        f"installed {RECIPE_NAME} at {path} "
        f"(destination={destination_source}, "
        "source=package:simpleclaw/runtime_assets/recipes/sports-live/recipe.yaml)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
