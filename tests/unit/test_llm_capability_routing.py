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
)
from simpleclaw.llm.profiles.base import ProviderProfile
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter


class _Provider(LLMProvider):
    async def send(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def __init__(self, name: str, *, error: Exception | None = None):
        response = LLMResponse(text="ok", backend_name=name, model="m")
        self.send = AsyncMock(side_effect=error) if error else AsyncMock(return_value=response)
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
