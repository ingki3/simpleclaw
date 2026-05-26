"""BIZ-298 — dreaming 프롬프트 YAML 외부화 로더.

운영자가 ``dreaming.py`` 의 코드를 손대지 않고도 LLM 프롬프트(시스템/유저)와
필수 변수 명세를 ``~/.simpleclaw/prompts/dreaming/{name}.yaml`` 에서 덮어쓸 수
있게 해 준다. 운영자 디렉터리에 파일이 없으면 패키지 내장 default
(``simpleclaw/memory/_prompts/{name}.yaml``) 로 fallback 한다.

설계 결정:
- 운영자 override 가 *우선* — 디스크에 파일이 있으면 무조건 그쪽을 쓴다. 잘못
  작성된 YAML 은 fail-closed (``PromptLoadError``) — 사이클 진입 직전에 발견되도록.
- ``required_vars`` 는 YAML 메타 필드와 ``user_prompt`` 안의 ``{var}`` placeholder
  집합이 *정확히* 일치할 때만 통과한다. 누락/오타로 인한 런타임 ``KeyError`` 를
  사이클이 절반쯤 진행된 뒤가 아니라 로드 시점에 잡는다.
- 한 프로세스(=한 dreaming 사이클) 안에서는 결과를 캐시한다. 운영자가 YAML 을
  수정하면 *다음* 사이클에 반영된다 (hot-reload 는 BIZ-298 범위 밖, 별도 sub).
- 패키지 내장 default 는 BIZ-298 시드 단계에서 현재 ``_DREAMING_PROMPT`` /
  ``_CLUSTER_SUMMARY_PROMPT`` 본문을 그대로 옮겨 놓은 형태. 파일별 분기와
  내용 분할은 BIZ-299 의 책임.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# 패키지 내장 default 디렉터리. ``simpleclaw/memory/_prompts/`` 에 6개 시드 YAML 이
# 함께 배포된다 (setuptools package-data 로 포함).
_PACKAGE_PROMPTS_DIR: Path = Path(__file__).parent / "_prompts"

# 운영자 override 디렉터리. 부재해도 무방하며, 운영자가 커스터마이즈할 때만 채워진다.
_DEFAULT_OPERATOR_PROMPTS_DIR: Path = Path("~/.simpleclaw/prompts/dreaming")


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


# 프로세스 단위 캐시. 키는 ``(name, operator_dir resolved str)``.
_CACHE: dict[tuple[str, str], DreamingPromptSpec] = {}


def load_dreaming_prompt(
    name: str,
    *,
    operator_dir: str | Path | None = None,
    refresh: bool = False,
) -> DreamingPromptSpec:
    """``{name}.yaml`` 을 운영자 디렉터리 → 패키지 내장 순으로 찾아 로드한다.

    Args:
        name: YAML basename (확장자 제외). 예: ``"memory"``, ``"cluster"``.
        operator_dir: 운영자 override 디렉터리. ``None`` 이면 기본 경로
            (``~/.simpleclaw/prompts/dreaming``) 사용. 테스트에서 격리 경로를
            주입하기 위해 노출.
        refresh: True 면 캐시를 무시하고 디스크에서 다시 읽는다.

    Returns:
        해석된 :class:`DreamingPromptSpec`.

    Raises:
        PromptLoadError: 운영자/패키지 양쪽 모두에 파일이 없거나, YAML 파싱/
            스키마 검증에 실패한 경우.
    """
    resolved_operator_dir = _resolve_operator_dir(operator_dir)
    cache_key = (name, str(resolved_operator_dir))
    if not refresh and cache_key in _CACHE:
        return _CACHE[cache_key]

    spec = _load_uncached(name, resolved_operator_dir)
    _CACHE[cache_key] = spec
    return spec


def clear_cache() -> None:
    """캐시를 비운다 (테스트 헬퍼)."""
    _CACHE.clear()


def _resolve_operator_dir(operator_dir: str | Path | None) -> Path:
    if operator_dir is None:
        return _DEFAULT_OPERATOR_PROMPTS_DIR.expanduser()
    return Path(operator_dir).expanduser()


def _load_uncached(name: str, operator_dir: Path) -> DreamingPromptSpec:
    operator_path = operator_dir / f"{name}.yaml"
    if operator_path.is_file():
        logger.debug("loading dreaming prompt %r from operator override: %s",
                     name, operator_path)
        return _parse_yaml(name, operator_path)

    package_path = _PACKAGE_PROMPTS_DIR / f"{name}.yaml"
    if package_path.is_file():
        return _parse_yaml(name, package_path)

    raise PromptLoadError(
        f"dreaming prompt {name!r} not found "
        f"(looked in operator dir {operator_path} and package default {package_path})"
    )


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
