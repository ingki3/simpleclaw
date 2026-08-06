"""Compatibility CLI for installing one manifest-declared runtime asset."""

from __future__ import annotations

import argparse
from pathlib import Path

from simpleclaw.production_assets import (
    ResolvedRuntimeAsset,
    install_runtime_asset,
    resolve_runtime_asset,
)

ASSET_REF = "recipe:sports-live"
RECIPE_NAME = ASSET_REF.partition(":")[2]


def _resolved() -> ResolvedRuntimeAsset:
    return resolve_runtime_asset(ASSET_REF)


CANONICAL_RECIPE = _resolved().root.joinpath("recipe.yaml")


def install(
    recipes_dir: Path | None = None,
    *,
    config_path: Path | None = None,
) -> Path:
    """Translate legacy arguments and delegate to the generic installer."""
    path, _ = install_runtime_asset(
        ASSET_REF,
        destination_parent=recipes_dir,
        config_path=config_path,
    )
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes-dir", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    path, resolved = install_runtime_asset(
        ASSET_REF,
        destination_parent=args.recipes_dir,
        config_path=args.config,
    )
    print(
        f"installed {resolved.manifest.ref} at {path} "
        f"(source={resolved.provenance}, "
        f"manifest_sha256={resolved.manifest.fingerprint})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
