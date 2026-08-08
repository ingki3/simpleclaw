"""BIZ-645 request-scoped temperature 전달 및 provider native mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.llm.cli_wrapper import CLIProvider
from simpleclaw.llm.models import LLMProviderError, LLMRequest, LLMResponse
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.claude import ClaudeProvider
from simpleclaw.llm.providers.gemini import GeminiProvider
from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.providers.vertex_gemini import VertexGeminiProvider
from simpleclaw.llm.router import LLMRouter


class _CapturingProvider:
    def __init__(self) -> None:
        self.send_kwargs: dict = {}
        self.stream_kwargs: dict = {}

    async def send(self, *_args, **kwargs) -> LLMResponse:
        self.send_kwargs = kwargs
        return LLMResponse(text="ok", backend_name="capture")

    async def stream(self, *_args, **kwargs) -> LLMResponse:
        self.stream_kwargs = kwargs
        return LLMResponse(text="ok", backend_name="capture")


@pytest.mark.asyncio
async def test_router_forwards_explicit_temperature_on_send_and_stream() -> None:
    provider = _CapturingProvider()
    router = LLMRouter(
        backends={}, providers={"capture": provider}, default_backend="capture"
    )

    await router.send(LLMRequest(user_message="send", temperature=0.0))

    async def on_delta(_delta: str) -> None:
        return None

    await router.send(
        LLMRequest(user_message="stream", temperature=0.0),
        on_text_delta=on_delta,
    )

    assert provider.send_kwargs["temperature"] == 0.0
    assert provider.stream_kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_router_omits_temperature_for_existing_callers() -> None:
    provider = _CapturingProvider()
    router = LLMRouter(
        backends={}, providers={"capture": provider}, default_backend="capture"
    )

    request = LLMRequest(user_message="legacy")
    await router.send(request)

    assert request.temperature is None
    assert "temperature" not in provider.send_kwargs


def _openai_response() -> MagicMock:
    return MagicMock(
        choices=[MagicMock(message=MagicMock(content="ok", tool_calls=None))],
        usage=None,
    )


@pytest.mark.asyncio
async def test_openai_explicit_temperature_wins_over_static_extra_body(
    monkeypatch,
) -> None:
    create = AsyncMock(return_value=_openai_response())
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )
    provider = OpenAIProvider(
        model="openai/gpt-5",
        api_key="test",
        name="openrouter",
        extra_body={"temperature": 0.8, "provider": {"sort": "latency"}},
    )

    await provider.send("", "hello", temperature=0.0)

    kwargs = create.call_args.kwargs
    assert kwargs["temperature"] == 0.0
    assert kwargs["extra_body"] == {"provider": {"sort": "latency"}}


@pytest.mark.asyncio
async def test_openai_none_preserves_static_temperature(monkeypatch) -> None:
    create = AsyncMock(return_value=_openai_response())
    client = MagicMock()
    client.chat.completions.create = create
    monkeypatch.setattr(
        "simpleclaw.llm.providers.openai_provider.openai.AsyncOpenAI",
        lambda **_: client,
    )
    provider = OpenAIProvider(
        model="openai/gpt-5",
        api_key="test",
        extra_body={"temperature": 0.8},
    )

    await provider.send("", "hello")

    kwargs = create.call_args.kwargs
    assert "temperature" not in kwargs
    assert kwargs["extra_body"] == {"temperature": 0.8}


def _gemini_response() -> SimpleNamespace:
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[SimpleNamespace(text="ok", function_call=None)]
                ),
                finish_reason=None,
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=1, candidates_token_count=1
        ),
        prompt_feedback=None,
    )


@pytest.mark.parametrize("provider_type", [GeminiProvider, VertexGeminiProvider])
@pytest.mark.asyncio
async def test_gemini_transports_map_native_temperature(provider_type) -> None:
    provider = object.__new__(provider_type)
    provider._model = "gemini-test"
    provider._name = "gemini-test"
    provider._profile = get_provider_profile("gemini")
    generate = AsyncMock(return_value=_gemini_response())
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    )

    await provider.send("", "hello", temperature=0.0)

    assert generate.call_args.kwargs["config"].temperature == 0.0


@pytest.mark.asyncio
async def test_claude_maps_native_temperature() -> None:
    provider = ClaudeProvider(model="claude-test", api_key="test")
    message = MagicMock(
        content=[MagicMock(type="text", text="ok")],
        usage=MagicMock(input_tokens=1, output_tokens=1),
    )
    del message.usage.cache_creation_input_tokens
    del message.usage.cache_read_input_tokens
    create = AsyncMock(return_value=message)
    provider._client.messages.create = create

    await provider.send("", "hello", temperature=0.0)

    assert create.call_args.kwargs["temperature"] == 0.0


@pytest.mark.asyncio
async def test_cli_rejects_required_temperature_before_execution() -> None:
    provider = CLIProvider(command="cat", name="test-cli")

    with pytest.raises(LLMProviderError, match="required temperature"):
        await provider.send("", "hello", temperature=0.0)
