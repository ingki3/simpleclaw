"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from simpleclaw.llm.models import LLMResponse


class LLMProvider(ABC):
    """Base class for all LLM providers (API and CLI)."""

    @abstractmethod
    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
    ) -> LLMResponse:
        """Send a message to the LLM and return the response."""
