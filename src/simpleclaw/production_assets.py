"""Domain-neutral runtime asset discovery, validation, and installation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml

MANIFEST_NAME = "runtime-asset.yaml"
DEFAULT_ASSET_ROOT = Path(__file__).resolve().parents[2] / "runtime_assets"
_ASSET_PART = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


class AssetResource(Protocol):
    """Subset shared by pathlib paths and importlib resources."""

    @property
    def name(self) -> str: ...

    def joinpath(self, *descendants: str) -> AssetResource: ...

    def is_file(self) -> bool: ...

    def read_bytes(self) -> bytes: ...


@dataclass(frozen=True)
class RuntimeAssetFile:
    """One verified file copied from an asset-local resource."""

    source: PurePosixPath
    destination: PurePosixPath
    sha256: str
    executable: bool = False


@dataclass(frozen=True)
class RuntimeAssetManifest:
    """Validated data-only installation contract."""

    asset_type: str
    name: str
    default_parent: Path
    config_path: Path | None
    config_keys: tuple[str, ...]
    files: tuple[RuntimeAssetFile, ...]
    fingerprint: str

    @property
    def ref(self) -> str:
        return f"{self.asset_type}:{self.name}"


@dataclass(frozen=True)
class ResolvedRuntimeAsset:
    """Manifest plus its source resource and provenance."""

    manifest: RuntimeAssetManifest
    root: AssetResource
    provenance: str


def _safe_relative(value: object, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{field} contains an unsafe path: {value!r}")
    return path


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a mapping")
    return value


def _load_manifest(resource: AssetResource) -> RuntimeAssetManifest:
    raw_bytes = resource.read_bytes()
    raw = _mapping(yaml.safe_load(raw_bytes), field="manifest")
    if raw.get("schema_version") != 1:
        raise ValueError("unsupported runtime asset manifest schema")
    asset = _mapping(raw.get("asset"), field="asset")
    asset_type = asset.get("type")
    name = asset.get("name")
    if not isinstance(asset_type, str) or not _ASSET_PART.fullmatch(asset_type):
        raise ValueError("asset.type is invalid")
    if not isinstance(name, str) or not _ASSET_PART.fullmatch(name):
        raise ValueError("asset.name is invalid")

    destination = _mapping(raw.get("destination"), field="destination")
    default_parent = destination.get("default_parent")
    if not isinstance(default_parent, str) or not default_parent:
        raise ValueError("destination.default_parent is required")
    config = destination.get("config")
    config_path: Path | None = None
    config_keys: tuple[str, ...] = ()
    if config is not None:
        config = _mapping(config, field="destination.config")
        raw_config_path = config.get("path")
        raw_keys = config.get("keys")
        if not isinstance(raw_config_path, str) or not raw_config_path:
            raise ValueError("destination.config.path is required")
        if (
            not isinstance(raw_keys, list)
            or not raw_keys
            or any(not isinstance(item, str) or not item for item in raw_keys)
        ):
            raise ValueError("destination.config.keys must be non-empty strings")
        config_path = Path(raw_config_path)
        config_keys = tuple(raw_keys)

    raw_files = raw.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("files must be a non-empty list")
    declared: list[RuntimeAssetFile] = []
    destinations: set[PurePosixPath] = set()
    for index, item in enumerate(raw_files):
        item = _mapping(item, field=f"files[{index}]")
        source = _safe_relative(item.get("source"), field=f"files[{index}].source")
        target = _safe_relative(
            item.get("destination"), field=f"files[{index}].destination"
        )
        digest = item.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"files[{index}].sha256 is invalid")
        if target in destinations:
            raise ValueError(f"duplicate destination: {target}")
        destinations.add(target)
        declared.append(
            RuntimeAssetFile(
                source=source,
                destination=target,
                sha256=digest,
                executable=bool(item.get("executable", False)),
            )
        )
    return RuntimeAssetManifest(
        asset_type=asset_type,
        name=name,
        default_parent=Path(default_parent),
        config_path=config_path,
        config_keys=config_keys,
        files=tuple(declared),
        fingerprint=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _asset_root() -> tuple[AssetResource, str]:
    if DEFAULT_ASSET_ROOT.is_dir():
        return DEFAULT_ASSET_ROOT, f"source:{DEFAULT_ASSET_ROOT}"
    packaged = files("simpleclaw").joinpath("runtime_assets")
    return packaged, "package:simpleclaw/runtime_assets"


def resolve_runtime_asset(
    asset: str | Path,
    *,
    assets_root: Path | None = None,
) -> ResolvedRuntimeAsset:
    """Resolve an asset ref or explicit manifest and verify every source digest."""
    if isinstance(asset, Path) or ":" not in str(asset):
        manifest_path = Path(asset).expanduser().resolve()
        resource: AssetResource = manifest_path
        root: AssetResource = manifest_path.parent
        provenance = f"manifest:{manifest_path}"
    else:
        asset_type, name = str(asset).split(":", maxsplit=1)
        if not _ASSET_PART.fullmatch(asset_type) or not _ASSET_PART.fullmatch(name):
            raise ValueError(f"invalid runtime asset ref: {asset!r}")
        if assets_root is None:
            catalog_root, provenance = _asset_root()
        else:
            catalog_root = assets_root.expanduser().resolve()
            provenance = f"source:{catalog_root}"
        root = catalog_root.joinpath(f"{asset_type}s", name)
        resource = root.joinpath(MANIFEST_NAME)
    if not resource.is_file():
        raise FileNotFoundError(f"runtime asset manifest not found: {asset}")
    manifest = _load_manifest(resource)
    if ":" in str(asset) and manifest.ref != str(asset):
        raise ValueError(
            f"runtime asset ref mismatch: expected {asset!s}, got {manifest.ref}"
        )
    for declared in manifest.files:
        source = root.joinpath(*declared.source.parts)
        if not source.is_file():
            raise FileNotFoundError(f"runtime asset source missing: {declared.source}")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != declared.sha256:
            raise ValueError(
                f"runtime asset source digest mismatch: {declared.source}"
            )
    return ResolvedRuntimeAsset(manifest, root, provenance)


def _configured_parent(
    manifest: RuntimeAssetManifest,
    *,
    config_path: Path | None,
) -> Path:
    parent = manifest.default_parent
    selected_config = config_path or manifest.config_path
    if selected_config is None:
        return parent.expanduser()
    selected_config = selected_config.expanduser()
    if not selected_config.is_file():
        return parent.expanduser()
    raw: object = yaml.safe_load(selected_config.read_text(encoding="utf-8")) or {}
    for key in manifest.config_keys:
        if not isinstance(raw, dict) or key not in raw:
            return parent.expanduser()
        raw = raw[key]
    if not isinstance(raw, str) or not raw:
        raise ValueError("configured runtime asset destination must be a path string")
    return Path(raw).expanduser()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def install_runtime_asset(
    asset: str | Path,
    *,
    destination_parent: Path | None = None,
    config_path: Path | None = None,
    assets_root: Path | None = None,
) -> tuple[Path, ResolvedRuntimeAsset]:
    """Validate fully, then atomically install an asset-owned directory."""
    resolved = resolve_runtime_asset(asset, assets_root=assets_root)
    manifest = resolved.manifest
    parent = (
        destination_parent.expanduser()
        if destination_parent is not None
        else _configured_parent(manifest, config_path=config_path)
    )
    destination = parent / manifest.name

    payload: dict[PurePosixPath, bytes] = {}
    for declared in manifest.files:
        payload[declared.destination] = resolved.root.joinpath(
            *declared.source.parts
        ).read_bytes()
    expected = {path.as_posix(): content for path, content in payload.items()}
    if _tree_bytes(destination) == expected:
        return destination, resolved

    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged = parent / f".{manifest.name}.staged-{token}"
    backup = parent / f".{manifest.name}.backup-{token}"
    try:
        for relative, content in payload.items():
            target = staged.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for declared in manifest.files:
            target = staged.joinpath(*declared.destination.parts)
            target.chmod(0o755 if declared.executable else 0o644)
        if _tree_bytes(staged) != expected:
            raise RuntimeError("staged runtime asset verification failed")
        if destination.exists():
            os.replace(destination, backup)
        os.replace(staged, destination)
        if backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if staged.exists():
            shutil.rmtree(staged)
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    return destination, resolved
