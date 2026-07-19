"""LLM transport implementation registry."""

from __future__ import annotations

from simpleclaw.llm.models import LLMConfigError

_TRANSPORT_REGISTRY: dict[str, type] = {}
_TRANSPORT_ALIASES = {
    "openai": "openai_chat",
    "openai_chat": "openai_chat",
    "chat_completions": "openai_chat",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    "vertex_gemini": "vertex_gemini",
    "cli": "cli",
}


def _normalize_key(value: str) -> str:
    return value.strip().lower()


def _ensure_registry() -> None:
    if _TRANSPORT_REGISTRY:
        return
    from simpleclaw.llm.cli_wrapper import CLIProvider
    from simpleclaw.llm.providers.claude import ClaudeProvider
    from simpleclaw.llm.providers.gemini import GeminiProvider
    from simpleclaw.llm.providers.openai_provider import OpenAIProvider
    from simpleclaw.llm.providers.vertex_gemini import VertexGeminiProvider

    _TRANSPORT_REGISTRY["anthropic"] = ClaudeProvider
    _TRANSPORT_REGISTRY["cli"] = CLIProvider
    _TRANSPORT_REGISTRY["gemini"] = GeminiProvider
    _TRANSPORT_REGISTRY["openai_chat"] = OpenAIProvider
    _TRANSPORT_REGISTRY["vertex_gemini"] = VertexGeminiProvider


def resolve_transport_name(name: str) -> str:
    """Resolve a transport alias to its canonical transport key."""
    key = _normalize_key(name)
    try:
        return _TRANSPORT_ALIASES[key]
    except KeyError as exc:
        known = ", ".join(sorted(_TRANSPORT_ALIASES))
        raise LLMConfigError(
            f"Unknown LLM transport '{name}'. Known transports: {known}"
        ) from exc


def get_transport_class(name: str) -> type:
    """Return the provider implementation class for a transport."""
    _ensure_registry()
    canonical = resolve_transport_name(name)
    try:
        return _TRANSPORT_REGISTRY[canonical]
    except KeyError as exc:
        known = ", ".join(sorted(_TRANSPORT_REGISTRY))
        raise LLMConfigError(
            f"No provider implementation registered for transport '{name}'. "
            f"Known transports: {known}"
        ) from exc


def list_transports() -> list[str]:
    """Return canonical transport keys with registered implementations."""
    _ensure_registry()
    return sorted(_TRANSPORT_REGISTRY)
