"""Gemini provider profile."""

from __future__ import annotations

from typing import Any

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.profiles.base import ProviderProfile

_REASONING_EFFORT_BUDGET_TOKENS = {"low": 256, "medium": 512, "high": 1024}


class GeminiProfile(ProviderProfile):
    """Gemini native schema dialect and thinking-policy adapter."""

    def sanitize_response_schema(self, schema: object) -> object:
        if isinstance(schema, dict):
            return {
                key: self.sanitize_response_schema(value)
                for key, value in schema.items()
                if key != "additionalProperties"
            }
        if isinstance(schema, list):
            return [self.sanitize_response_schema(item) for item in schema]
        return schema

    def reasoning_budget(self, reasoning: dict[str, Any] | None) -> int | None:
        if not isinstance(reasoning, dict) or not reasoning.get("enabled"):
            return None
        budget = reasoning.get("budget_tokens")
        if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0:
            return budget
        effort = str(reasoning.get("effort") or "medium").strip().lower()
        return _REASONING_EFFORT_BUDGET_TOKENS.get(
            effort, _REASONING_EFFORT_BUDGET_TOKENS["medium"]
        )


class GeminiOpenAIProfile(ProviderProfile):
    """Gemini's opt-in OpenAI-compatible endpoint adapter.

    This profile intentionally does not claim native replay or reasoning parity.
    The OpenAI-compatible endpoint shares the Chat Completions wire transport,
    but those Gemini-native protocol features need credential-gated evidence
    before they may be used by a route requiring them.
    """

    def sanitize_response_schema(self, schema: object) -> object:
        """Remove Gemini-native ``propertyOrdering`` without mutating input."""
        if isinstance(schema, dict):
            return {
                key: self.sanitize_response_schema(value)
                for key, value in schema.items()
                if key != "propertyOrdering"
            }
        if isinstance(schema, list):
            return [self.sanitize_response_schema(item) for item in schema]
        return schema


GEMINI_PROFILE = GeminiProfile(
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

# Google exposes this endpoint at ``https://generativelanguage.googleapis.com/
# v1beta/openai/``.  It is deliberately opt-in: the native ``gemini`` profile
# stays the only route with native replay/thinking guarantees.
GEMINI_OPENAI_PROFILE = GeminiOpenAIProfile(
    name="gemini-openai",
    default_transport="openai_chat",
    aliases=("gemini_openai",),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        structured_output=True,
        multimodal=True,
    ),
    request_extra_keys=("base_url", "extra_body", "default_headers"),
)
