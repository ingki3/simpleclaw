"""LLM provider profile registry tests."""

from __future__ import annotations

import pytest

from simpleclaw.llm.models import LLMConfigError
from simpleclaw.llm.profiles import get_provider_profile, list_provider_profiles


def test_builtin_profiles_are_explicitly_registered():
    assert list_provider_profiles() == [
        "anthropic",
        "gemini",
        "gemini-openai",
        "generic",
        "openai",
        "openrouter",
    ]


def test_profile_aliases_resolve_to_canonical_profiles():
    assert get_provider_profile("openai").name == "openai"
    assert get_provider_profile("openrouter").name == "openrouter"
    assert get_provider_profile("google").name == "gemini"
    assert get_provider_profile("claude").name == "anthropic"


def test_generic_profile_is_model_independent():
    profile = get_provider_profile("generic")

    assert profile.default_transport == "openai_chat"
    assert profile.capabilities.structured_output is True
    # Arbitrary model IDs stay opaque to profiles; they are backend config data.
    assert "model" not in profile.request_extra_keys


def test_unknown_profile_raises_actionable_config_error():
    with pytest.raises(LLMConfigError, match="Unknown LLM provider profile"):
        get_provider_profile("not-a-provider")
