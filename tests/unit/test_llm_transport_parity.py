"""Characterization tests for transport/profile preserving router behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.models import LLMRequest, LLMResponse, ToolCall, ToolDefinition
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.providers.openai_provider import _sanitize_json_schema_for_openai
from simpleclaw.llm.router import LLMRouter


class _ParityProvider(LLMProvider):
    """Provider double that returns identical normalized tool calls on both paths."""

    async def send(self, *args, **kwargs):  # pragma: no cover - replaced per instance
        raise NotImplementedError

    def __init__(self) -> None:
        response = LLMResponse(
            text="",
            backend_name="mock",
            model="opaque-model-id",
            tool_calls=[
                ToolCall(
                    id="call_123",
                    name="lookup",
                    arguments={"q": "weather"},
                )
            ],
        )
        self.send = AsyncMock(return_value=response)
        self.stream = AsyncMock(return_value=response)


@pytest.mark.asyncio
async def test_router_preserves_tool_call_ids_for_send_and_stream():
    provider = _ParityProvider()
    router = LLMRouter(
        backends={},
        providers={"mock": provider},
        default_backend="mock",
    )
    tool = ToolDefinition(
        name="lookup",
        description="lookup facts",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    send_response = await router.send(LLMRequest(user_message="x", tools=[tool]))

    async def _on_delta(_: str) -> None:
        pass

    stream_response = await router.send(
        LLMRequest(user_message="x", tools=[tool]),
        on_text_delta=_on_delta,
    )

    assert send_response.tool_calls == stream_response.tool_calls
    assert send_response.tool_calls[0].id == "call_123"
    assert provider.send.call_args.args[3] == [tool]
    assert provider.stream.call_args.args[3] == [tool]


def test_structured_schema_sanitizers_do_not_mutate_original_schema():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "propertyOrdering": ["answer"],
        "properties": {
            "answer": {
                "type": "object",
                "additionalProperties": False,
                "propertyOrdering": ["text"],
                "properties": {"text": {"type": "string"}},
            }
        },
    }

    gemini_schema = get_provider_profile("gemini").adapt_schema(schema)
    openai_schema = _sanitize_json_schema_for_openai(schema)

    assert "additionalProperties" in schema
    assert "propertyOrdering" in schema
    assert "additionalProperties" not in gemini_schema
    assert "propertyOrdering" in gemini_schema
    assert "propertyOrdering" not in openai_schema
    assert "additionalProperties" in openai_schema
