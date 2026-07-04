"""시스템 프롬프트 YAML 로더.

운영 중 LLM에 직접 전달되는 시스템/보조 프롬프트는 코드 상수 대신
레포 루트의 ``prompts/system/{name}.yaml`` 에서 관리한다. 코드에는 로딩과
변수 치환만 남겨 프롬프트 변경을 PR diff로 명확히 리뷰할 수 있게 한다.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT_ENV = "SIMPLECLAW_ROOT"
_REPO_ROOT_MARKER = "pyproject.toml"
_PROMPTS_SUBPATH: tuple[str, ...] = ("prompts", "system")
_TEXT_FIELDS = frozenset(("prompt", "system_prompt", "user_prompt", "template"))


class PromptLoadError(RuntimeError):
    """시스템 프롬프트 YAML 로드/검증 실패."""


@dataclass(frozen=True)
class SystemPromptSpec:
    """YAML에서 로드한 시스템 프롬프트 명세."""

    name: str
    version: int
    description: str
    fields: dict[str, str]
    required_vars: tuple[str, ...]
    source_path: Path

    @property
    def prompt(self) -> str:
        """단일 ``prompt`` 필드를 반환한다."""
        if "prompt" not in self.fields:
            raise PromptLoadError(f"system prompt {self.name!r} has no 'prompt' field")
        return self.fields["prompt"]

    @property
    def system_prompt(self) -> str:
        """``system_prompt`` 필드를 반환한다."""
        if "system_prompt" not in self.fields:
            raise PromptLoadError(f"system prompt {self.name!r} has no 'system_prompt' field")
        return self.fields["system_prompt"]

    @property
    def user_prompt(self) -> str:
        """``user_prompt`` 필드를 반환한다."""
        if "user_prompt" not in self.fields:
            raise PromptLoadError(f"system prompt {self.name!r} has no 'user_prompt' field")
        return self.fields["user_prompt"]

    def field(self, name: str) -> str:
        """지정한 텍스트 필드를 반환한다."""
        try:
            return self.fields[name]
        except KeyError as exc:
            raise PromptLoadError(
                f"system prompt {self.name!r} has no {name!r} field"
            ) from exc

    def format_field(self, name: str = "prompt", **kwargs: object) -> str:
        """지정 필드를 ``str.format`` 으로 렌더링한다."""
        missing = [var for var in self.required_vars if var not in kwargs]
        if missing:
            raise PromptLoadError(
                f"system prompt {self.name!r} missing required vars: {missing}"
            )
        try:
            return self.field(name).format(**kwargs)
        except KeyError as exc:
            raise PromptLoadError(
                f"system prompt {self.name!r} format failed "
                f"(undeclared placeholder): {exc}"
            ) from exc


_CACHE: dict[tuple[str, str], SystemPromptSpec] = {}
_FORMAT_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_system_prompt(
    name: str,
    *,
    repo_root: str | Path | None = None,
    refresh: bool = False,
) -> SystemPromptSpec:
    """``<repo_root>/prompts/system/{name}.yaml`` 을 로드한다."""
    resolved_root = _resolve_repo_root(repo_root)
    cache_key = (name, str(resolved_root))
    if not refresh and cache_key in _CACHE:
        return _CACHE[cache_key]

    spec = _load_uncached(name, resolved_root)
    _CACHE[cache_key] = spec
    return spec


def clear_cache() -> None:
    """테스트용 프롬프트 캐시 초기화."""
    _CACHE.clear()


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    """repo root 를 결정한다. 명시값 > env > pyproject walk-up."""
    if repo_root is not None:
        return Path(repo_root).expanduser().resolve()

    env_value = os.environ.get(_REPO_ROOT_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()

    found = _walk_up_for(Path(__file__).resolve(), _REPO_ROOT_MARKER)
    if found is None:
        raise PromptLoadError(
            f"could not resolve simpleclaw repo root: {_REPO_ROOT_ENV} env unset "
            f"and no {_REPO_ROOT_MARKER} found walking up from {Path(__file__).resolve()}"
        )
    return found


def _walk_up_for(start: Path, marker: str) -> Path | None:
    """``start`` 부모를 거슬러 올라가 marker 파일을 가진 첫 디렉터리 반환."""
    for candidate in (start, *start.parents):
        if (candidate / marker).is_file():
            return candidate
    return None


def _load_uncached(name: str, repo_root: Path) -> SystemPromptSpec:
    path = repo_root.joinpath(*_PROMPTS_SUBPATH, f"{name}.yaml")
    if not path.is_file():
        raise PromptLoadError(
            f"system prompt {name!r} not found at {path} (repo_root={repo_root})"
        )
    logger.debug("loading system prompt %r from %s", name, path)
    return _parse_yaml(name, path)


def _extract_format_vars(text: str) -> set[str]:
    """``str.format`` placeholder 이름 집합을 추출한다."""
    safe = text.replace("{{", "\x00").replace("}}", "\x01")
    return {match.group(1) for match in _FORMAT_FIELD_RE.finditer(safe)}


def _parse_yaml(name: str, path: Path) -> SystemPromptSpec:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptLoadError(
            f"system prompt {name!r}: could not read {path}: {exc}"
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PromptLoadError(
            f"system prompt {name!r}: invalid YAML at {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise PromptLoadError(
            f"system prompt {name!r}: root must be a mapping "
            f"(got {type(data).__name__}) at {path}"
        )

    fields = {key: value for key, value in data.items() if key in _TEXT_FIELDS}
    if not fields:
        raise PromptLoadError(
            f"system prompt {name!r}: missing one of {sorted(_TEXT_FIELDS)} at {path}"
        )
    for key, value in fields.items():
        if not isinstance(value, str):
            raise PromptLoadError(
                f"system prompt {name!r}: {key} must be a string at {path}"
            )

    version_raw = data.get("version", 1)
    try:
        version = int(version_raw)
    except (TypeError, ValueError) as exc:
        raise PromptLoadError(
            f"system prompt {name!r}: version must be int at {path}: {exc}"
        ) from exc

    required_vars_raw = data.get("required_vars", [])
    if required_vars_raw is None:
        required_vars_raw = []
    if not isinstance(required_vars_raw, list):
        raise PromptLoadError(
            f"system prompt {name!r}: required_vars must be a list "
            f"(got {type(required_vars_raw).__name__}) at {path}"
        )
    required_vars = tuple(str(var) for var in required_vars_raw)

    declared = set(required_vars)
    actual: set[str] = set()
    for value in fields.values():
        actual.update(_extract_format_vars(value))
    if declared != actual:
        raise PromptLoadError(
            f"system prompt {name!r}: required_vars do not match placeholders at {path} — "
            f"missing_in_declared={sorted(actual - declared)}, "
            f"extra_in_declared={sorted(declared - actual)}"
        )

    return SystemPromptSpec(
        name=str(data.get("name") or name),
        version=version,
        description=str(data.get("description", "")),
        fields=fields,
        required_vars=required_vars,
        source_path=path,
    )
