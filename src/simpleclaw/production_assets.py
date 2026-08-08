"""도메인 중립 runtime asset을 탐색·검증하고 원자적으로 설치한다."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
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
    """파일 경로와 패키지 resource가 공유하는 최소 읽기 계약이다."""

    @property
    def name(self) -> str:
        """resource의 파일 이름을 반환한다."""
        ...

    def joinpath(self, *descendants: str) -> AssetResource:
        """하위 resource를 같은 추상화로 반환한다."""
        ...

    def is_file(self) -> bool:
        """resource가 읽을 수 있는 일반 파일인지 반환한다."""
        ...

    def read_bytes(self) -> bytes:
        """resource 내용을 bytes로 읽는다."""
        ...


@dataclass(frozen=True)
class RuntimeAssetFile:
    """asset 내부에서 검증 후 복사할 파일 하나를 선언한다."""

    source: PurePosixPath
    destination: PurePosixPath
    sha256: str
    executable: bool = False


@dataclass(frozen=True)
class RuntimeAssetManifest:
    """검증을 마친 data-only 설치 계약이다."""

    asset_type: str
    name: str
    default_parent: Path
    config_path: Path | None
    config_keys: tuple[str, ...]
    files: tuple[RuntimeAssetFile, ...]
    fingerprint: str

    @property
    def ref(self) -> str:
        """manifest의 안정적인 ``type:name`` 식별자를 반환한다."""
        return f"{self.asset_type}:{self.name}"


@dataclass(frozen=True)
class ResolvedRuntimeAsset:
    """manifest와 검증된 source bytes 및 provenance를 묶는다."""

    manifest: RuntimeAssetManifest
    root: AssetResource
    provenance: str
    source_bytes: tuple[bytes, ...]


def _safe_relative(value: object, *, field: str) -> PurePosixPath:
    """manifest 경로를 안전한 POSIX 상대 경로로 제한한다."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{field} contains an unsafe path: {value!r}")
    return path


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    """manifest 필드가 mapping인지 확인해 이후 파싱을 단순화한다."""
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a mapping")
    return value


def _load_manifest(raw_bytes: bytes) -> RuntimeAssetManifest:
    """검증을 마친 manifest bytes의 전체 schema를 파싱한다."""
    raw = _mapping(yaml.safe_load(raw_bytes), field="manifest")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
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
        executable = item.get("executable", False)
        if type(executable) is not bool:
            raise TypeError(f"files[{index}].executable must be a boolean")
        declared.append(
            RuntimeAssetFile(
                source=source,
                destination=target,
                sha256=digest,
                executable=executable,
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
    """source checkout을 우선하고 없으면 package resource를 선택한다."""
    if DEFAULT_ASSET_ROOT.is_dir():
        return DEFAULT_ASSET_ROOT, f"source:{DEFAULT_ASSET_ROOT}"
    packaged = files("simpleclaw").joinpath("runtime_assets")
    return packaged, "package:simpleclaw/runtime_assets"


def _read_regular_asset_file(
    root: AssetResource,
    relative: PurePosixPath,
    *,
    kind: str = "source",
) -> bytes:
    """symlink를 따르지 않고 asset root 내부의 일반 파일만 읽는다."""
    source = root.joinpath(*relative.parts)
    if isinstance(root, Path) and isinstance(source, Path):
        root_resolved = root.resolve(strict=True)
        try:
            source_stat = source.lstat()
            source_resolved = source.resolve(strict=True)
            source_resolved.relative_to(root_resolved)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise ValueError(
                f"runtime asset {kind} escapes asset root: {relative}"
            ) from exc
        expected = root_resolved.joinpath(*relative.parts)
        if source_resolved != expected or not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(
                f"runtime asset {kind} must be a contained regular file: {relative}"
            )
    else:
        is_symlink = getattr(source, "is_symlink", None)
        if callable(is_symlink) and is_symlink():
            raise ValueError(
                f"runtime asset {kind} must not be a symlink: {relative}"
            )
        if not source.is_file():
            raise FileNotFoundError(f"runtime asset {kind} missing: {relative}")
    return source.read_bytes()


def resolve_runtime_asset(
    asset: str | Path,
    *,
    assets_root: Path | None = None,
) -> ResolvedRuntimeAsset:
    """asset ref 또는 manifest를 해석하고 모든 source digest를 검증한다."""
    if isinstance(asset, Path) or ":" not in str(asset):
        manifest_input = Path(asset).expanduser()
        root = manifest_input.parent.resolve(strict=True)
        manifest_relative = PurePosixPath(manifest_input.name)
        manifest_path = root / manifest_input.name
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
        manifest_relative = PurePosixPath(MANIFEST_NAME)
    try:
        manifest_bytes = _read_regular_asset_file(
            root,
            manifest_relative,
            kind="manifest",
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"runtime asset manifest not found: {asset}") from exc
    manifest = _load_manifest(manifest_bytes)
    if ":" in str(asset) and manifest.ref != str(asset):
        raise ValueError(
            f"runtime asset ref mismatch: expected {asset!s}, got {manifest.ref}"
        )
    source_bytes: list[bytes] = []
    for declared in manifest.files:
        content = _read_regular_asset_file(root, declared.source)
        actual = hashlib.sha256(content).hexdigest()
        if actual != declared.sha256:
            raise ValueError(
                f"runtime asset source digest mismatch: {declared.source}"
            )
        source_bytes.append(content)
    return ResolvedRuntimeAsset(manifest, root, provenance, tuple(source_bytes))


def _configured_parent(
    manifest: RuntimeAssetManifest,
    *,
    config_path: Path | None,
) -> Path:
    """runtime config와 manifest 기본값에서 설치 상위 경로를 결정한다."""
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


@dataclass(frozen=True)
class _TreeState:
    """symlink를 포함하지 않는 설치 트리의 완전한 상태를 표현한다."""

    directories: frozenset[str]
    files: dict[str, tuple[bytes, int]]


def _tree_state(root: Path) -> _TreeState | None:
    """root와 모든 하위 항목을 symlink 추적 없이 검증해 읽는다."""
    try:
        root_mode = root.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(root_mode):
        return None

    directories: set[str] = set()
    files: dict[str, tuple[bytes, int]] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_stat = entry.stat(follow_symlinks=False)
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if stat.S_ISDIR(entry_stat.st_mode):
                    directories.add(relative)
                    pending.append(path)
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    return None
                files[relative] = (
                    path.read_bytes(),
                    stat.S_IMODE(entry_stat.st_mode),
                )
    return _TreeState(frozenset(directories), files)


def _expected_tree_state(
    files: dict[str, tuple[bytes, int]],
) -> _TreeState:
    """manifest leaf에서 필요한 중간 디렉터리까지 포함한 상태를 만든다."""
    directories = {
        parent.as_posix()
        for relative in files
        for parent in PurePosixPath(relative).parents
        if parent != PurePosixPath(".")
    }
    return _TreeState(frozenset(directories), files)


def _remove_owned_tree(root: Path) -> None:
    """installer 소유 트리를 symlink 추적 없이 제거한다."""
    if root.is_symlink() or not root.is_dir():
        root.unlink(missing_ok=True)
        return
    with os.scandir(root) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                _remove_owned_tree(path)
            else:
                path.unlink()
    root.rmdir()


def install_runtime_asset(
    asset: str | Path,
    *,
    destination_parent: Path | None = None,
    config_path: Path | None = None,
    assets_root: Path | None = None,
) -> tuple[Path, ResolvedRuntimeAsset]:
    """전체 계약 검증 후 asset 소유 디렉터리를 원자적으로 설치한다."""
    resolved = resolve_runtime_asset(asset, assets_root=assets_root)
    manifest = resolved.manifest
    parent = (
        destination_parent.expanduser()
        if destination_parent is not None
        else _configured_parent(manifest, config_path=config_path)
    )
    destination = parent / manifest.name

    payload: dict[PurePosixPath, bytes] = {}
    for declared, content in zip(manifest.files, resolved.source_bytes, strict=True):
        payload[declared.destination] = content
    expected = _expected_tree_state(
        {
            declared.destination.as_posix(): (
                payload[declared.destination],
                0o755 if declared.executable else 0o644,
            )
            for declared in manifest.files
        }
    )
    if _tree_state(destination) == expected:
        return destination, resolved

    parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    staged = parent / f".{manifest.name}.staged-{token}"
    backup = parent / f".{manifest.name}.backup-{token}"
    destination_moved = False
    replacement_installed = False
    try:
        for relative, content in payload.items():
            target = staged.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for declared in manifest.files:
            target = staged.joinpath(*declared.destination.parts)
            target.chmod(0o755 if declared.executable else 0o644)
        if _tree_state(staged) != expected:
            raise RuntimeError("staged runtime asset verification failed")
        if destination.exists():
            os.replace(destination, backup)
            destination_moved = True
        os.replace(staged, destination)
        replacement_installed = True
        if _tree_state(destination) != expected:
            raise RuntimeError("installed runtime asset verification failed")
        if backup.is_symlink():
            _remove_owned_tree(backup)
        elif backup.exists():
            shutil.rmtree(backup)
    except BaseException:
        if destination_moved:
            if replacement_installed and destination.exists():
                os.replace(destination, staged)
            if backup.exists():
                os.replace(backup, destination)
        if staged.exists():
            _remove_owned_tree(staged)
        raise
    return destination, resolved


def verify_runtime_asset_installation(
    asset: str | Path,
    *,
    destination_parent: Path | None = None,
    config_path: Path | None = None,
    assets_root: Path | None = None,
) -> tuple[Path, ResolvedRuntimeAsset]:
    """설치 destination이 canonical manifest tree와 exact match인지 검증한다."""
    resolved = resolve_runtime_asset(asset, assets_root=assets_root)
    manifest = resolved.manifest
    parent = (
        destination_parent.expanduser()
        if destination_parent is not None
        else _configured_parent(manifest, config_path=config_path)
    )
    destination = parent / manifest.name
    expected = _expected_tree_state(
        {
            declared.destination.as_posix(): (
                content,
                0o755 if declared.executable else 0o644,
            )
            for declared, content in zip(
                manifest.files,
                resolved.source_bytes,
                strict=True,
            )
        }
    )
    if _tree_state(destination) != expected:
        raise ValueError(f"runtime asset installation drift: {manifest.ref}")
    return destination, resolved
