"""Data models for the LLM routing layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class BackendType(Enum):
    """Type of LLM backend."""
    API = "api"
    CLI = "cli"


@dataclass
class LLMBackend:
    """Configuration for a single LLM backend."""
    name: str
    backend_type: BackendType
    model: str
    api_key_env: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    timeout: int = 120


@dataclass
class LLMRequest:
    """A request to send to an LLM."""
    system_prompt: str = ""
    user_message: str = ""
    backend_name: str | None = None
    messages: list[dict] | None = None


@dataclass
class LLMResponse:
    """A response from an LLM."""
    text: str = ""
    backend_name: str = ""
    model: str = ""
    usage: dict | None = None


# Error classes

class LLMError(Exception):
    """Base class for LLM errors."""


class LLMConfigError(LLMError):
    """Configuration error (missing config, unknown backend, etc.)."""


class LLMAuthError(LLMError):
    """Authentication error (missing or invalid API key)."""


class LLMProviderError(LLMError):
    """Provider error (API call failure, network error, etc.)."""


class LLMTimeoutError(LLMError):
    """CLI process timeout."""


class LLMCLINotFoundError(LLMError):
    """CLI tool not found on the system."""
