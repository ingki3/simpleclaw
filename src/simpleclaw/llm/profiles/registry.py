"""Built-in provider profile registry."""

from __future__ import annotations

from simpleclaw.llm.models import LLMConfigError
from simpleclaw.llm.profiles.anthropic import ANTHROPIC_PROFILE
from simpleclaw.llm.profiles.base import ProviderProfile
from simpleclaw.llm.profiles.gemini import GEMINI_OPENAI_PROFILE, GEMINI_PROFILE
from simpleclaw.llm.profiles.openai_compatible import (
    GENERIC_PROFILE,
    OPENAI_PROFILE,
    OPENROUTER_PROFILE,
)

_BUILTIN_PROFILES: tuple[ProviderProfile, ...] = (
    OPENAI_PROFILE,
    OPENROUTER_PROFILE,
    GEMINI_PROFILE,
    GEMINI_OPENAI_PROFILE,
    ANTHROPIC_PROFILE,
    GENERIC_PROFILE,
)

_PROFILE_REGISTRY: dict[str, ProviderProfile] = {}
_PROFILE_ALIASES: dict[str, str] = {}


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _ensure_registry() -> None:
    if _PROFILE_REGISTRY:
        return
    for profile in _BUILTIN_PROFILES:
        _PROFILE_REGISTRY[profile.name] = profile
        _PROFILE_ALIASES[profile.name] = profile.name
        for alias in profile.aliases:
            _PROFILE_ALIASES[_normalize_key(alias)] = profile.name


def resolve_profile_name(name: str) -> str:
    """Resolve a profile alias to its canonical name."""
    _ensure_registry()
    key = _normalize_key(name)
    try:
        return _PROFILE_ALIASES[key]
    except KeyError as exc:
        known = ", ".join(sorted(_PROFILE_REGISTRY))
        raise LLMConfigError(
            f"Unknown LLM provider profile '{name}'. Known profiles: {known}"
        ) from exc


def get_provider_profile(name: str) -> ProviderProfile:
    """Return a built-in provider profile by canonical name or alias."""
    _ensure_registry()
    return _PROFILE_REGISTRY[resolve_profile_name(name)]


def list_provider_profiles() -> list[str]:
    """Return canonical built-in provider profile names."""
    _ensure_registry()
    return sorted(_PROFILE_REGISTRY)
