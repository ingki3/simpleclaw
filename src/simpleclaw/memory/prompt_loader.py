"""BIZ-301 — dreaming 프롬프트 YAML 로더 (repo-root SoT).

dreaming 의 system/user prompt 와 required_vars 명세는 레포 루트 아래
``prompts/dreaming/{name}.yaml`` 단일 위치에서만 관리한다. 운영자는 이 파일을
직접 편집 + PR 으로 반영하며, 별도의 운영자 override 디렉터리나 패키지 내장
fallback 은 없다 (BIZ-298 의 2단 fallback 폐지).

설계 결정:
- *Source of truth = repo root* — git 으로 추적/리뷰되는 first-class 파일. 같은
  콘텐츠를 두 곳에서 유지하지 않는다.
- repo root 해소는 ``SIMPLECLAW_ROOT`` env 우선, 없으면 모듈 위치에서 ``pyproject.toml``
  까지 walk-up. 둘 다 실패하면 ``PromptLoadError`` (fail-closed).
- ``required_vars`` 는 ``user_prompt`` 안의 ``{var}`` placeholder 집합과 *정확히*
  일치할 때만 통과. 런타임 ``KeyError`` 를 로드 시점에 잡는다.
- 한 프로세스(=한 dreaming 사이클) 안에서는 결과를 캐시한다. 운영자가 YAML 을
  수정해도 데몬 재시작 / 다음 사이클까지는 반영되지 않는다 (hot-reload 는 별도 sub).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# repo root 를 명시적으로 주입할 때 쓰는 env. 테스트 격리 / 운영 배포 분리용.
_REPO_ROOT_ENV: str = "SIMPLECLAW_ROOT"

# walk-up 시 발견하면 그 디렉터리를 repo root 로 채택할 marker.
_REPO_ROOT_MARKER: str = "pyproject.toml"

# repo root 아래 dreaming 프롬프트 디렉터리 (SoT).
_PROMPTS_SUBPATH: tuple[str, ...] = ("prompts", "dreaming")


class PromptLoadError(RuntimeError):
    """프롬프트 YAML 로드/검증 실패. fail-closed — 사이클 abort 트리거."""


@dataclass(frozen=True)
class DreamingPromptSpec:
    """한 dreaming 프롬프트 (system + user template) 의 메모리 표현.

    ``format(**vars)`` 로 user template 을 채운 최종 문자열을 얻는다.
    """

    name: str
    version: int
    description: str
    system_prompt: str
    user_prompt: str
    required_vars: tuple[str, ...]
    source_path: Path

    def format(self, **kwargs: object) -> str:
        """user_prompt 를 ``str.format`` 으로 채운다.

        ``required_vars`` 중 하나라도 ``kwargs`` 에 빠지면 ``PromptLoadError``.
        포맷 도중 발견된 ``KeyError`` (선언되지 않은 placeholder) 도 동일하게 변환.
        """
        missing = [v for v in self.required_vars if v not in kwargs]
        if missing:
            raise PromptLoadError(
                f"prompt {self.name!r} missing required vars: {missing}"
            )
        try:
            return self.user_prompt.format(**kwargs)
        except KeyError as exc:
            raise PromptLoadError(
                f"prompt {self.name!r} format failed (undeclared placeholder): {exc}"
            ) from exc


# 프로세스 단위 캐시. 키는 ``(name, repo_root resolved str)``.
_CACHE: dict[tuple[str, str], DreamingPromptSpec] = {}


def load_dreaming_prompt(
    name: str,
    *,
    repo_root: str | Path | None = None,
    refresh: bool = False,
) -> DreamingPromptSpec:
    """``<repo_root>/prompts/dreaming/{name}.yaml`` 을 로드한다.

    Args:
        name: YAML basename (확장자 제외). 예: ``"memory"``, ``"cluster"``.
        repo_root: repo root override. ``None`` 이면 ``SIMPLECLAW_ROOT`` env →
            ``pyproject.toml`` walk-up 순으로 해소. 테스트가 격리된 레포 트리를
            주입할 때 명시적으로 전달.
        refresh: True 면 캐시를 무시하고 디스크에서 다시 읽는다.

    Returns:
        해석된 :class:`DreamingPromptSpec`.

    Raises:
        PromptLoadError: repo root 해소 실패, YAML 파일 부재, 또는 YAML 파싱/
            스키마 검증 실패 시.
    """
    resolved_root = _resolve_repo_root(repo_root)
    cache_key = (name, str(resolved_root))
    if not refresh and cache_key in _CACHE:
        return _CACHE[cache_key]

    spec = _load_uncached(name, resolved_root)
    _CACHE[cache_key] = spec
    return spec


def clear_cache() -> None:
    """캐시를 비운다 (테스트 헬퍼)."""
    _CACHE.clear()


def _resolve_repo_root(repo_root: str | Path | None) -> Path:
    """repo root 를 결정한다. override > env > walk-up.

    walk-up 은 본 모듈 위치에서 시작하여 부모 디렉터리를 거슬러 올라가며
    ``pyproject.toml`` 을 찾는다. editable install (`pip install -e .`) 과
    source clone 양쪽에서 동작.
    """
    if repo_root is not None:
        return Path(repo_root).expanduser().resolve()

    env_value = os.environ.get(_REPO_ROOT_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()

    found = _walk_up_for(Path(__file__).resolve(), _REPO_ROOT_MARKER)
    if found is None:
        raise PromptLoadError(
            f"could not resolve simpleclaw repo root: "
            f"{_REPO_ROOT_ENV} env unset and no {_REPO_ROOT_MARKER} found "
            f"walking up from {Path(__file__).resolve()}"
        )
    return found


def _walk_up_for(start: Path, marker: str) -> Path | None:
    """``start`` 의 부모를 거슬러 올라가며 marker 파일을 가진 첫 디렉터리를 반환."""
    for candidate in (start, *start.parents):
        if (candidate / marker).is_file():
            return candidate
    return None


def _load_uncached(name: str, repo_root: Path) -> DreamingPromptSpec:
    path = repo_root.joinpath(*_PROMPTS_SUBPATH, f"{name}.yaml")
    if not path.is_file():
        raise PromptLoadError(
            f"dreaming prompt {name!r} not found at {path} "
            f"(repo_root={repo_root})"
        )
    logger.debug("loading dreaming prompt %r from %s", name, path)
    return _parse_yaml(name, path)


_FORMAT_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _extract_format_vars(text: str) -> set[str]:
    """``str.format`` placeholder 이름 집합을 추출한다.

    ``{{`` / ``}}`` (리터럴 brace) 는 이스케이프이므로 매칭에서 제외해야 한다.
    이를 위해 임시 sentinel 로 치환한 뒤 정규식을 적용한다.
    """
    safe = text.replace("{{", "\x00").replace("}}", "\x01")
    return {m.group(1) for m in _FORMAT_FIELD_RE.finditer(safe)}


def _parse_yaml(name: str, path: Path) -> DreamingPromptSpec:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptLoadError(
            f"dreaming prompt {name!r}: could not read {path}: {exc}"
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PromptLoadError(
            f"dreaming prompt {name!r}: invalid YAML at {path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise PromptLoadError(
            f"dreaming prompt {name!r}: root must be a mapping "
            f"(got {type(data).__name__}) at {path}"
        )

    for field in ("system_prompt", "user_prompt"):
        if field not in data:
            raise PromptLoadError(
                f"dreaming prompt {name!r}: missing required field {field!r} at {path}"
            )

    system_prompt = data["system_prompt"]
    user_prompt = data["user_prompt"]
    if not isinstance(system_prompt, str) or not isinstance(user_prompt, str):
        raise PromptLoadError(
            f"dreaming prompt {name!r}: system_prompt and user_prompt must be strings "
            f"at {path}"
        )

    version_raw = data.get("version", 1)
    try:
        version = int(version_raw)
    except (TypeError, ValueError) as exc:
        raise PromptLoadError(
            f"dreaming prompt {name!r}: version must be int at {path}: {exc}"
        ) from exc

    description = str(data.get("description", ""))

    required_vars_raw = data.get("required_vars", [])
    if required_vars_raw is None:
        required_vars_raw = []
    if not isinstance(required_vars_raw, list):
        raise PromptLoadError(
            f"dreaming prompt {name!r}: required_vars must be a list "
            f"(got {type(required_vars_raw).__name__}) at {path}"
        )
    required_vars = tuple(str(v) for v in required_vars_raw)

    declared = set(required_vars)
    actual = _extract_format_vars(user_prompt)
    if declared != actual:
        missing_in_declared = sorted(actual - declared)
        extra_in_declared = sorted(declared - actual)
        raise PromptLoadError(
            f"dreaming prompt {name!r}: required_vars do not match user_prompt "
            f"placeholders at {path} — "
            f"missing_in_declared={missing_in_declared}, "
            f"extra_in_declared={extra_in_declared}"
        )

    return DreamingPromptSpec(
        name=name,
        version=version,
        description=description,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        required_vars=required_vars,
        source_path=path,
    )
