"""Install a manifest-declared runtime asset."""

from __future__ import annotations

import argparse
from pathlib import Path

from simpleclaw.production_assets import install_runtime_asset


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    path, resolved = install_runtime_asset(
        args.asset,
        destination_parent=args.destination,
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

