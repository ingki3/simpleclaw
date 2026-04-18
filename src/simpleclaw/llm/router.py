"""LLM router: config-driven backend selection and message routing."""

from __future__ import annotations

import logging
from pathlib import Path

from simpleclaw.config import load_llm_config
from simpleclaw.llm.models import (
    BackendType,
    LLMBackend,
    LLMConfigError,
    LLMRequest,
    LLMResponse,
)
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

_PROVIDER_REGISTRY: dict[str, type] = {}


def _ensure_registry() -> None:
    """Lazily populate the provider registry."""
    if _PROVIDER_REGISTRY:
        return
    # Import here to avoid circular imports and allow optional deps
    from simpleclaw.llm.providers.claude import ClaudeProvider
    from simpleclaw.llm.providers.openai_provider import OpenAIProvider
    from simpleclaw.llm.providers.gemini import GeminiProvider
    from simpleclaw.llm.cli_wrapper import CLIProvider

    _PROVIDER_REGISTRY["claude"] = ClaudeProvider
    _PROVIDER_REGISTRY["openai"] = OpenAIProvider
    _PROVIDER_REGISTRY["gemini"] = GeminiProvider
    _PROVIDER_REGISTRY["cli"] = CLIProvider


class LLMRouter:
    """Routes LLM requests to the appropriate backend."""

    def __init__(
        self,
        backends: dict[str, LLMBackend],
        providers: dict[str, LLMProvider],
        default_backend: str,
    ) -> None:
        self._backends = backends
        self._providers = providers
        self._default = default_backend

    async def send(self, request: LLMRequest) -> LLMResponse:
        """Route a request to the appropriate backend and return the response."""
        backend_name = request.backend_name or self._default

        if backend_name not in self._providers:
            raise LLMConfigError(
                f"Unknown backend '{backend_name}'. "
                f"Available: {', '.join(self._providers.keys())}"
            )

        provider = self._providers[backend_name]
        logger.info("Routing request to backend '%s'", backend_name)

        response = await provider.send(
            request.system_prompt, request.user_message, request.messages
        )
        return response

    def list_backends(self) -> list[str]:
        """Return names of all registered backends."""
        return list(self._providers.keys())

    def get_default_backend(self) -> str:
        """Return the name of the default backend."""
        return self._default


def create_router(config_path: str | Path) -> LLMRouter:
    """Create an LLMRouter from config.yaml settings."""
    _ensure_registry()

    config = load_llm_config(config_path)
    default_name = config.get("default", "")
    providers_config = config.get("providers", {})

    if not providers_config:
        raise LLMConfigError("No LLM providers configured in config.yaml")

    backends: dict[str, LLMBackend] = {}
    providers: dict[str, LLMProvider] = {}

    for name, pconf in providers_config.items():
        backend_type_str = pconf.get("type", "api")
        try:
            backend_type = BackendType(backend_type_str)
        except ValueError:
            raise LLMConfigError(
                f"Invalid backend type '{backend_type_str}' for provider '{name}'"
            )

        backend = LLMBackend(
            name=name,
            backend_type=backend_type,
            model=pconf.get("model", ""),
            api_key_env=pconf.get("api_key_env"),
            command=pconf.get("command"),
            args=pconf.get("args", []),
            timeout=pconf.get("timeout", 120),
        )
        backends[name] = backend

        if backend_type == BackendType.CLI:
            provider_cls = _PROVIDER_REGISTRY.get("cli")
            if provider_cls:
                provider = provider_cls(
                    command=backend.command,
                    args=backend.args,
                    timeout=backend.timeout,
                    name=name,
                )
                providers[name] = provider
        else:
            # API provider — match by name or fall back to generic
            provider_cls = _PROVIDER_REGISTRY.get(name)
            if provider_cls:
                api_key = pconf.get("api_key", "")
                try:
                    provider = provider_cls(
                        model=backend.model,
                        api_key=api_key,
                        name=name,
                    )
                    providers[name] = provider
                except Exception as exc:
                    logger.warning(
                        "Skipping provider '%s': %s", name, exc
                    )
            else:
                logger.warning(
                    "No provider implementation for '%s', skipping.", name
                )

    if default_name and default_name not in providers:
        logger.warning(
            "Default backend '%s' not available. Using first available.",
            default_name,
        )
        default_name = next(iter(providers)) if providers else ""

    if not providers:
        raise LLMConfigError("No valid LLM providers could be initialized")

    return LLMRouter(
        backends=backends,
        providers=providers,
        default_backend=default_name,
    )
