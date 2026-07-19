"""Anthropic provider profile."""

from __future__ import annotations

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.profiles.base import ProviderProfile

ANTHROPIC_PROFILE = ProviderProfile(
    name="anthropic",
    default_transport="anthropic",
    aliases=("claude",),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        native_replay=True,
    ),
)
