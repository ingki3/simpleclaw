"""등록 skill 전용 child-process secret 환경변수 구성.

``security.skill_env_secret_refs``는 전역 ``os.environ``이나 다른 subprocess
경로를 거치지 않고, 정확히 지정된 등록 skill 실행에만 전달할 환경변수를 만든다.
설정 오류는 startup 단계에서 fail-closed하며 예외와 로그에는 reference나 해소된
값을 포함하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

from simpleclaw.security.secrets import SecretReference, default_manager

_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_ALLOWED_REFERENCE_SCHEMES = frozenset({"env", "file", "keyring"})


class _SecretResolver(Protocol):
    """SecretsManager의 이 기능에 필요한 최소 인터페이스."""

    def resolve(self, reference: str) -> str:
        """시크릿 참조를 실제 값으로 해소한다."""


class SkillEnvConfigError(ValueError):
    """skill 전용 secret binding 설정이 안전하게 적용될 수 없음을 알린다."""


def _is_valid_env_name(name: object) -> bool:
    """POSIX 호환 대문자 환경변수 이름인지 검사한다."""
    return isinstance(name, str) and bool(_ENV_NAME_PATTERN.fullmatch(name))


def validate_env_overrides(
    env_overrides: Mapping[str, str] | None,
) -> dict[str, str]:
    """executor가 받을 child env override를 방어적으로 복사·검증한다.

    loader 외 호출자가 executor를 직접 사용할 수 있으므로 subprocess 생성 직전에도
    같은 이름 경계를 적용한다. 오류에는 값이나 잘못된 key 원문을 넣지 않는다.
    """
    if env_overrides is None:
        return {}
    if not isinstance(env_overrides, Mapping):
        raise TypeError("Invalid skill environment override mapping")

    validated: dict[str, str] = {}
    for env_name, value in env_overrides.items():
        if not _is_valid_env_name(env_name):
            raise ValueError("Invalid skill environment override name")
        if not isinstance(value, str) or not value:
            raise ValueError("Invalid skill environment override value")
        validated[env_name] = value
    return validated


def load_skill_env_secret_refs(
    raw_config: object,
    *,
    manager: _SecretResolver | None = None,
) -> dict[str, dict[str, str]]:
    """skill별 secret reference를 child env override map으로 해소한다.

    허용 형식은 ``{skill_name: {ENV_NAME: "file:key"}}``이며 ``env:``,
    ``file:``, ``keyring:`` 참조만 받는다. 평문 및 ``plain:`` 값은 config에
    credential이 복제되는 경로이므로 거부한다.

    Args:
        raw_config: ``security.skill_env_secret_refs`` 원본 설정.
        manager: 테스트 또는 부트스트랩에서 주입할 secret resolver.

    Returns:
        정확한 skill 이름을 key로 하는 child env override의 독립 사본.

    Raises:
        SkillEnvConfigError: 구조·이름·참조가 잘못됐거나 값을 해소할 수 없을 때.
    """
    if raw_config is None:
        return {}
    if not isinstance(raw_config, Mapping):
        raise SkillEnvConfigError("Invalid skill secret binding configuration")

    resolver = manager if manager is not None else default_manager()
    resolved_by_skill: dict[str, dict[str, str]] = {}

    for skill_name, env_refs in raw_config.items():
        if (
            not isinstance(skill_name, str)
            or not skill_name
            or skill_name != skill_name.strip()
        ):
            raise SkillEnvConfigError("Invalid skill secret binding configuration")
        if not isinstance(env_refs, Mapping):
            raise SkillEnvConfigError("Invalid skill secret binding configuration")

        resolved_env: dict[str, str] = {}
        for env_name, reference in env_refs.items():
            if not _is_valid_env_name(env_name):
                raise SkillEnvConfigError("Invalid skill secret binding configuration")
            if not isinstance(reference, str) or not reference:
                raise SkillEnvConfigError("Invalid skill secret binding configuration")

            parsed = SecretReference.parse(reference)
            if (
                parsed is None
                or parsed.scheme not in _ALLOWED_REFERENCE_SCHEMES
                or not parsed.name
            ):
                raise SkillEnvConfigError("Invalid skill secret reference")

            value = resolver.resolve(reference)
            if not value:
                raise SkillEnvConfigError("Unable to resolve configured skill secret")
            resolved_env[env_name] = value

        resolved_by_skill[skill_name] = resolved_env

    return resolved_by_skill
