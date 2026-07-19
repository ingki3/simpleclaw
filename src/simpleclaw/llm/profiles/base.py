"""Provider profile contracts.

Profiles describe provider API semantics independently from a concrete model ID.
Transports describe the implementation class used to talk to that API shape.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from simpleclaw.llm.capabilities import LLMCapabilities


@dataclass(frozen=True)
class ProviderProfile:
    """Static provider API profile.

    ``name`` is the canonical profile key. ``default_transport`` is the transport
    used when legacy config supplies only ``provider``/backend name. Explicit
    ``transport`` config always wins over this default.
    """

    name: str
    default_transport: str
    aliases: tuple[str, ...] = ()
    capabilities: LLMCapabilities = field(default_factory=LLMCapabilities)
    request_extra_keys: tuple[str, ...] = ()

    def request_extras(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return provider-profile extras copied from a static backend config."""
        return {
            key: copy.deepcopy(config[key])
            for key in self.request_extra_keys
            if key in config
        }

    def build_request_extras(
        self, reasoning: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build request-body extras from provider-neutral runtime hints.

        Runtime callers cannot provide arbitrary provider payloads. Profiles may
        translate the small canonical hint surface into endpoint-specific fields.
        """
        del reasoning
        return {}

    def adapt_schema(self, schema: object) -> object:
        """Return a profile-compatible copy of a structured-output schema."""
        return self.sanitize_response_schema(schema)

    def reasoning_budget(self, reasoning: dict[str, Any] | None) -> int | None:
        """Return a native reasoning budget, or None when the hint is optional/no-op."""
        del reasoning
        return None

    def sanitize_response_schema(self, schema: object) -> object:
        """Profile hook for schema normalization.

        Built-in providers currently perform final SDK-specific sanitation inside
        their transport implementations. The hook exists so route migration can
        move that logic without changing the provider-neutral request model.
        """
        return copy.deepcopy(schema)
