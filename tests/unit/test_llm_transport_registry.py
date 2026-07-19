"""LLM transport registry and config normalization tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.config_sections.llm import load_llm_config
from simpleclaw.llm.models import LLMConfigError
from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.router import create_router
from simpleclaw.llm.transports import get_transport_class, list_transports


def test_builtin_transport_registry_contains_current_implementations():
    assert list_transports() == [
        "anthropic",
        "cli",
        "gemini",
        "openai_chat",
        "vertex_gemini",
    ]
    assert get_transport_class("openai_chat") is OpenAIProvider
    assert get_transport_class("openai") is OpenAIProvider


def test_unknown_transport_raises_actionable_config_error():
    with pytest.raises(LLMConfigError, match="Unknown LLM transport"):
        get_transport_class("not-a-transport")


def test_openai_responses_extension_is_explicitly_not_registered(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  providers:
    future_responses:
      type: api
      model: gpt-future
      transport: openai_responses
      profile: openai
      api_key: test-key
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigError, match="openai_responses.*not registered"):
        create_router(config)


def test_legacy_provider_config_normalizes_to_transport_profile(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  providers:
    openai:
      provider: openai
      type: api
      model: gpt-4o
      api_key: test-key
""".strip(),
        encoding="utf-8",
    )

    llm = load_llm_config(config)

    assert llm["providers"]["openai"]["transport"] == "openai_chat"
    assert llm["providers"]["openai"]["profile"] == "openai"


def test_explicit_transport_profile_values_win(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: openrouter
  providers:
    openrouter:
      provider: openai
      transport: openai_chat
      profile: openrouter
      type: api
      model: z-ai/glm-5.2
      api_key: test-key
      base_url: https://openrouter.ai/api/v1
""".strip(),
        encoding="utf-8",
    )

    llm = load_llm_config(config)
    router = create_router(config)

    assert llm["providers"]["openrouter"]["transport"] == "openai_chat"
    assert llm["providers"]["openrouter"]["profile"] == "openrouter"
    assert router.get_backend_profile("openrouter").name == "openrouter"


def test_openai_and_openrouter_share_transport_with_distinct_profiles(
    tmp_path: Path,
):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: direct_openai
  providers:
    direct_openai:
      transport: openai_chat
      profile: openai
      type: api
      model: gpt-4o
      api_key: test-openai-key
    openrouter_deepseek:
      transport: openai_chat
      profile: openrouter
      type: api
      model: deepseek/deepseek-chat-v4
      api_key: test-openrouter-key
      base_url: https://openrouter.ai/api/v1
""".strip(),
        encoding="utf-8",
    )

    router = create_router(config)

    assert isinstance(router._providers["direct_openai"], OpenAIProvider)
    assert isinstance(router._providers["openrouter_deepseek"], OpenAIProvider)
    assert router._backends["direct_openai"].transport == "openai_chat"
    assert router._backends["openrouter_deepseek"].transport == "openai_chat"
    assert router.get_backend_profile("direct_openai").name == "openai"
    assert router.get_backend_profile("openrouter_deepseek").name == "openrouter"
