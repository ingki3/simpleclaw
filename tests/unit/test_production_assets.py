"""Domain-neutral runtime asset resolver and installer contracts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

import simpleclaw.production_assets as production_assets
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


def test_source_symlink_escape_fails_before_destination_change(tmp_path: Path) -> None:
    asset = _asset_tree(tmp_path)
    outside = asset.parent / "outside-secret"
    outside.write_bytes(b"external secret\n")
    source = asset / "payload.txt"
    source.unlink()
    source.symlink_to("../outside-secret")
    manifest_path = asset / "runtime-asset.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    destination = tmp_path / "installed/comet"
    destination.mkdir(parents=True)
    (destination / "sentinel").write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime asset source"):
        install_runtime_asset("widget:comet", assets_root=tmp_path)

    assert (destination / "sentinel").read_text(encoding="utf-8") == "old"
    assert not (destination / "nested/payload.txt").exists()


def test_package_traversable_symlink_source_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TraversableResource:
        def __init__(self, path: Path) -> None:
            self.path = path

        @property
        def name(self) -> str:
            return self.path.name

        def joinpath(self, *descendants: str) -> TraversableResource:
            return TraversableResource(self.path.joinpath(*descendants))

        def is_file(self) -> bool:
            return self.path.is_file()

        def is_symlink(self) -> bool:
            return self.path.is_symlink()

        def read_bytes(self) -> bytes:
            return self.path.read_bytes()

    asset = _asset_tree(tmp_path)
    outside = asset.parent / "outside-secret"
    outside.write_bytes(b"package escape\n")
    source = asset / "payload.txt"
    source.unlink()
    source.symlink_to("../outside-secret")
    manifest_path = asset / "runtime-asset.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        production_assets,
        "_asset_root",
        lambda: (TraversableResource(tmp_path), "package:test"),
    )

    with pytest.raises(ValueError, match="must not be a symlink"):
        resolve_runtime_asset("widget:comet")


@pytest.mark.parametrize("fault", ("write", "move_old", "install_new", "cleanup"))
def test_install_fault_restores_destination_without_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    _asset_tree(tmp_path)
    destination = tmp_path / "installed/comet"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("old", encoding="utf-8")

    original_write_bytes = Path.write_bytes
    original_replace = os.replace

    if fault == "write":
        def fail_write(path: Path, data: bytes) -> int:
            if ".staged-" in str(path):
                raise OSError("injected staged write failure")
            return original_write_bytes(path, data)

        monkeypatch.setattr(Path, "write_bytes", fail_write)
    elif fault in {"move_old", "install_new"}:
        def fail_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
            source_path = Path(source)
            if fault == "move_old" and source_path == destination:
                raise OSError("injected destination move failure")
            if fault == "install_new" and ".staged-" in source_path.name:
                raise OSError("injected replacement failure")
            original_replace(source, target)

        monkeypatch.setattr(production_assets.os, "replace", fail_replace)
    else:
        def fail_backup_cleanup(path: os.PathLike[str]) -> None:
            raise OSError("injected backup cleanup failure")

        monkeypatch.setattr(production_assets.shutil, "rmtree", fail_backup_cleanup)

    with pytest.raises(OSError, match="injected"):
        install_runtime_asset("widget:comet", assets_root=tmp_path)

    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (destination / "nested/payload.txt").exists()
    assert not list(destination.parent.glob(".comet.staged-*"))
    assert not list(destination.parent.glob(".comet.backup-*"))
