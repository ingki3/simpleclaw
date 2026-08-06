"""Compatibility CLI for installing one manifest-declared runtime asset."""

from __future__ import annotations

import argparse
from pathlib import Path

from simpleclaw.production_assets import (
    ResolvedRuntimeAsset,
    install_runtime_asset,
    resolve_runtime_asset,
)

ASSET_REF = "skill:naver-sports-skill"
SKILL_NAME = ASSET_REF.partition(":")[2]


def _resolved() -> ResolvedRuntimeAsset:
    return resolve_runtime_asset(ASSET_REF)


CANONICAL_SKILL_MD = _resolved().root.joinpath("SKILL.md")


def install(global_dir: Path | None = None) -> Path:
    """Translate the legacy destination and delegate to the generic installer."""
    path, _ = install_runtime_asset(ASSET_REF, destination_parent=global_dir)
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-dir", type=Path)
    args = parser.parse_args(argv)
    path, resolved = install_runtime_asset(
        ASSET_REF,
        destination_parent=args.global_dir,
    )
    print(
        f"installed {resolved.manifest.ref} at {path} "
        f"(source={resolved.provenance}, "
        f"manifest_sha256={resolved.manifest.fingerprint})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
