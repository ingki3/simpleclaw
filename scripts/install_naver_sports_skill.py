"""Naver Sports skill 설치 인자를 generic installer로 변환한다."""

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
    """호환 상수가 참조할 canonical skill asset을 해석한다."""
    return resolve_runtime_asset(ASSET_REF)


CANONICAL_SKILL_MD = _resolved().root.joinpath("SKILL.md")


def install(global_dir: Path | None = None) -> Path:
    """기존 destination 인자를 generic installer 계약으로 변환한다."""
    path, _ = install_runtime_asset(ASSET_REF, destination_parent=global_dir)
    return path


def main(argv: list[str] | None = None) -> int:
    """기존 CLI 표면을 유지하면서 generic installer를 호출한다."""
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
