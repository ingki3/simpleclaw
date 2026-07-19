"""Gemini provider profile."""

from __future__ import annotations

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.profiles.base import ProviderProfile

GEMINI_PROFILE = ProviderProfile(
    name="gemini",
    default_transport="gemini",
    aliases=("google", "google_ai"),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        structured_output=True,
        multimodal=True,
        reasoning=True,
        native_replay=True,
    ),
)
