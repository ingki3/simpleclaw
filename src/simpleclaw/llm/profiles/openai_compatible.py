"""OpenAI-compatible provider profiles."""

from __future__ import annotations

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.profiles.base import ProviderProfile

_OPENAI_COMPATIBLE_EXTRAS = ("base_url", "extra_body", "default_headers")

OPENAI_PROFILE = ProviderProfile(
    name="openai",
    default_transport="openai_chat",
    aliases=("chatgpt",),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        structured_output=True,
        native_replay=True,
    ),
    request_extra_keys=_OPENAI_COMPATIBLE_EXTRAS,
)

OPENROUTER_PROFILE = ProviderProfile(
    name="openrouter",
    default_transport="openai_chat",
    aliases=("openrouter.ai",),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        structured_output=True,
        native_replay=True,
    ),
    request_extra_keys=_OPENAI_COMPATIBLE_EXTRAS,
)

GENERIC_PROFILE = ProviderProfile(
    name="generic",
    default_transport="openai_chat",
    aliases=("openai_compatible", "compatible"),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        structured_output=True,
    ),
    request_extra_keys=_OPENAI_COMPATIBLE_EXTRAS,
)
