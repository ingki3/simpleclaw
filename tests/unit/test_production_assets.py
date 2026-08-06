"""Domain-neutral runtime asset resolver and installer contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from simpleclaw.production_assets import install_runtime_asset, resolve_runtime_asset


def _asset_tree(root: Path, *, name: str = "comet") -> Path:
    asset = root / "widgets" / name
    asset.mkdir(parents=True)
    payload = b"verified payload\n"
    (asset / "payload.txt").write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "asset": {"type": "widget", "name": name},
        "destination": {"default_parent": str(root / "installed")},
        "files": [
            {
                "source": "payload.txt",
                "destination": "nested/payload.txt",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    (asset / "runtime-asset.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return asset


def test_arbitrary_manifest_asset_installs_without_core_registration(
    tmp_path: Path,
) -> None:
    _asset_tree(tmp_path)

    destination, resolved = install_runtime_asset(
        "widget:comet",
        assets_root=tmp_path,
    )

    assert resolved.manifest.ref == "widget:comet"
    assert (destination / "nested/payload.txt").read_bytes() == b"verified payload\n"


def test_install_is_idempotent_and_custom_destination_is_exact(tmp_path: Path) -> None:
    _asset_tree(tmp_path)
    parent = tmp_path / "custom"

    first, _ = install_runtime_asset(
        "widget:comet", assets_root=tmp_path, destination_parent=parent
    )
    inode = first.stat().st_ino
    second, _ = install_runtime_asset(
        "widget:comet", assets_root=tmp_path, destination_parent=parent
    )

    assert second == parent / "comet"
    assert second.stat().st_ino == inode


@pytest.mark.parametrize(
    "mutation",
    ("digest", "missing", "source_traversal", "destination_traversal"),
)
def test_invalid_asset_fails_before_destination_change(
    tmp_path: Path,
    mutation: str,
) -> None:
    asset = _asset_tree(tmp_path)
    destination = tmp_path / "installed/comet"
    destination.mkdir(parents=True)
    sentinel = destination / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    manifest_path = asset / "runtime-asset.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if mutation == "digest":
        manifest["files"][0]["sha256"] = "0" * 64
    elif mutation == "missing":
        (asset / "payload.txt").unlink()
    elif mutation == "source_traversal":
        manifest["files"][0]["source"] = "../secret"
    else:
        manifest["files"][0]["destination"] = "../escape"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises((FileNotFoundError, TypeError, ValueError)):
        resolve_runtime_asset("widget:comet", assets_root=tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (tmp_path / "installed/escape").exists()
