"""OpenAI-compatible provider profiles."""

from __future__ import annotations

import copy
from typing import Any

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.profiles.base import ProviderProfile

_OPENAI_COMPATIBLE_EXTRAS = ("base_url", "extra_body", "default_headers")


class OpenRouterProfile(ProviderProfile):
    """OpenRouter Chat Completions quirks independent from model IDs."""

    _UNSUPPORTED_SCHEMA_KEYS = frozenset({"maxItems", "propertyOrdering"})

    def sanitize_response_schema(self, schema: object) -> object:
        """OpenRouter upstream이 거부하는 schema 확장 키를 복사본에서 제거한다.

        OpenRouter의 OpenAI-shaped ``json_schema``는 선택된 upstream dialect로
        다시 변환된다. Google upstream은 전체 UnifiedTurnPlan에서 ``maxItems``를
        ``INVALID_ARGUMENT``로 거부하므로 profile 경계에서 제거한다. 배열 길이
        제한은 provider-neutral 원본 schema와 runtime parser clamp에 유지된다.
        """
        if isinstance(schema, dict):
            return {
                key: self.sanitize_response_schema(value)
                for key, value in schema.items()
                if key not in self._UNSUPPORTED_SCHEMA_KEYS
            }
        if isinstance(schema, list):
            return [self.sanitize_response_schema(item) for item in schema]
        return copy.deepcopy(schema)

    def build_request_extras(
        self, reasoning: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not isinstance(reasoning, dict) or not reasoning.get("enabled"):
            return {}
        return {"reasoning": copy.deepcopy(reasoning)}


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

OPENROUTER_PROFILE = OpenRouterProfile(
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

OPENROUTER_MULTIMODAL_PROFILE = OpenRouterProfile(
    name="openrouter-multimodal",
    default_transport="openai_chat",
    aliases=("openrouter_multimodal",),
    capabilities=LLMCapabilities(
        tools=True,
        streaming=True,
        structured_output=True,
        multimodal=True,
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
