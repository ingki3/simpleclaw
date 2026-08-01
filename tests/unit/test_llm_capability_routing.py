"""Route capability preflight prevents incompatible network calls."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.capabilities import LLMCapabilities
from simpleclaw.llm.models import (
    LLMConfigError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMRoute,
    MultimodalAttachment,
    ToolDefinition,
)
from simpleclaw.llm.profiles.base import ProviderProfile
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter


class _Provider(LLMProvider):
    async def send(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def __init__(self, name: str, *, error: Exception | None = None):
        response = LLMResponse(text="ok", backend_name=name, model="m")
        self.send = (
            AsyncMock(side_effect=error) if error else AsyncMock(return_value=response)
        )
        self.stream = AsyncMock(return_value=response)


def _profile(name: str, **capabilities: bool) -> ProviderProfile:
    return ProviderProfile(
        name=name,
        default_transport="test",
        capabilities=LLMCapabilities(**capabilities),
    )


@pytest.mark.asyncio
async def test_required_structured_output_rejects_incompatible_primary():
    provider = _Provider("plain")
    router = LLMRouter(
        backends={},
        providers={"plain": provider},
        profiles={"plain": _profile("plain")},
        default_backend="plain",
    )
    with pytest.raises(LLMConfigError, match="structured_output"):
        await router.send(
            LLMRequest(
                backend_name="plain",
                require_structured_output=True,
                response_schema={"type": "object"},
            )
        )
    provider.send.assert_not_called()


@pytest.mark.asyncio
async def test_incompatible_retry_is_skipped_and_primary_error_is_preserved():
    primary = _Provider("primary", error=LLMProviderError("primary down"))
    retry = _Provider("retry")
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        profiles={
            "primary": _profile("primary", structured_output=True),
            "retry": _profile("retry"),
        },
        default_backend="primary",
        routes={"json": LLMRoute("json", "primary", "retry")},
    )
    with pytest.raises(LLMProviderError, match="primary down"):
        await router.send(
            LLMRequest(
                route_name="json",
                require_structured_output=True,
                response_schema={"type": "object"},
            )
        )
    retry.send.assert_not_called()


@pytest.mark.asyncio
async def test_multimodal_route_requires_multimodal_capability():
    provider = _Provider("text_only")
    router = LLMRouter(
        backends={},
        providers={"text_only": provider},
        profiles={"text_only": _profile("text_only")},
        default_backend="text_only",
        routes={"multimodal": LLMRoute("multimodal", "text_only")},
    )
    request = LLMRequest(
        route_name="multimodal",
        messages=[
            {
                "role": "user",
                "attachments": [
                    MultimodalAttachment(data=b"image", mime_type="image/png")
                ],
            }
        ],
    )
    with pytest.raises(LLMConfigError, match="multimodal"):
        await router.send(request)
    provider.send.assert_not_called()


@pytest.mark.asyncio
async def test_openrouter_multimodal_profile_passes_combined_preflight():
    provider = _Provider("openrouter_gemini")
    router = LLMRouter(
        backends={},
        providers={"openrouter_gemini": provider},
        profiles={"openrouter_gemini": get_provider_profile("openrouter-multimodal")},
        default_backend="openrouter_gemini",
        routes={"multimodal": LLMRoute("multimodal", "openrouter_gemini")},
    )
    request = LLMRequest(
        route_name="multimodal",
        messages=[
            {
                "role": "user",
                "content": "Inspect.",
                "attachments": [
                    MultimodalAttachment(data=b"image", mime_type="image/png")
                ],
            }
        ],
        tools=[
            ToolDefinition(
                name="lookup",
                description="Lookup a value.",
                parameters={"type": "object", "properties": {}},
            )
        ],
        require_structured_output=True,
        response_schema={"type": "object"},
    )

    response = await router.send(request)

    assert response.backend_name == "openrouter_gemini"
    provider.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_plain_openrouter_profile_cannot_be_selected_for_multimodal():
    provider = _Provider("plain_openrouter")
    router = LLMRouter(
        backends={},
        providers={"plain_openrouter": provider},
        profiles={"plain_openrouter": get_provider_profile("openrouter")},
        default_backend="plain_openrouter",
        routes={"multimodal": LLMRoute("multimodal", "plain_openrouter")},
    )

    with pytest.raises(LLMConfigError, match="multimodal"):
        await router.send(
            LLMRequest(
                route_name="multimodal",
                messages=[
                    {
                        "role": "user",
                        "attachments": [
                            MultimodalAttachment(data=b"image", mime_type="image/png")
                        ],
                    }
                ],
            )
        )

    provider.send.assert_not_called()


@pytest.mark.asyncio
async def test_openrouter_multimodal_route_does_not_implicitly_retry_native_gemini():
    openrouter = _Provider(
        "openrouter_gemini", error=LLMProviderError("openrouter unavailable")
    )
    native_gemini = _Provider("native_gemini")
    router = LLMRouter(
        backends={},
        providers={
            "openrouter_gemini": openrouter,
            "native_gemini": native_gemini,
        },
        profiles={
            "openrouter_gemini": get_provider_profile("openrouter-multimodal"),
            "native_gemini": _profile("gemini", multimodal=True),
        },
        default_backend="openrouter_gemini",
        routes={"multimodal": LLMRoute("multimodal", "openrouter_gemini")},
    )

    with pytest.raises(LLMProviderError, match="openrouter unavailable"):
        await router.send(
            LLMRequest(
                route_name="multimodal",
                messages=[
                    {
                        "role": "user",
                        "attachments": [
                            MultimodalAttachment(data=b"image", mime_type="image/png")
                        ],
                    }
                ],
            )
        )

    native_gemini.send.assert_not_called()
