"""Provider profile capability contracts for LLM routing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMCapabilities:
    """Model-independent capabilities exposed by a provider profile."""

    tools: bool = False
    streaming: bool = False
    structured_output: bool = False
    multimodal: bool = False
    reasoning: bool = False
    native_replay: bool = False
