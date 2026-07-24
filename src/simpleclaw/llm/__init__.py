"""LLM routing layer with multi-provider support and CLI wrapping."""

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.models import (
    BackendType,
    LLMAuthError,
    LLMBackend,
    LLMCLINotFoundError,
    LLMConfigError,
    LLMError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
)
from simpleclaw.llm.profiles import ProviderProfile

__all__ = [
    "BackendType",
    "LLMAuthError",
    "LLMBackend",
    "LLMCLINotFoundError",
    "LLMCapabilities",
    "LLMConfigError",
    "LLMError",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMRouter",
    "LLMTimeoutError",
    "ProviderProfile",
    "create_router",
]


def __getattr__(name: str):
    """Lazy router exports to avoid config-loader import cycles."""
    if name in {"LLMRouter", "create_router"}:
        from simpleclaw.llm.router import LLMRouter, create_router

        return {"LLMRouter": LLMRouter, "create_router": create_router}[name]
    raise AttributeError(name)
