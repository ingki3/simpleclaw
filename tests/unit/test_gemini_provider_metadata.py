"""Gemini provider finish diagnostics preservation tests.

AI Studio exports can show `finishReason: STOP` with zero output tokens even when the
model returns an empty final answer. The provider should preserve that metadata in a
provider-neutral `LLMResponse` so tool-loop fallback logs can explain what happened.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.llm.providers.gemini import GeminiProvider


def _text_part(text: str = "") -> SimpleNamespace:
    """Gemini-like text Part with no function call."""
    return SimpleNamespace(function_call=None, text=text)


def _response(*, text: str = "", finish_reason: str = "STOP") -> SimpleNamespace:
    """Build a minimal Gemini-like response object for provider parsing tests."""
    content = SimpleNamespace(parts=[_text_part(text)])
    candidate = SimpleNamespace(
        content=content,
        finish_reason=finish_reason,
        safety_ratings=[],
    )
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(
            prompt_token_count=15073,
            candidates_token_count=len(text.split()),
        ),
        prompt_feedback=None,
    )


@pytest.mark.asyncio
async def test_send_preserves_empty_stop_finish_reason_and_zero_output_tokens():
    """Empty STOP responses should keep finish reason and zero-output diagnostics."""
    provider = GeminiProvider(model="gemini-3.5-flash", api_key="k")
    generate = AsyncMock(return_value=_response(text="", finish_reason="STOP"))
    provider._client.aio.models.generate_content = generate

    result = await provider.send("system", "user")

    assert result.text == ""
    assert result.finish_reason == "STOP"
    assert result.usage == {"input_tokens": 15073, "output_tokens": 0}
    assert result.diagnostics == {
        "finish_reason": "STOP",
        "prompt_token_count": 15073,
        "candidates_token_count": 0,
        "empty_output_tokens": True,
    }


@pytest.mark.asyncio
async def test_send_preserves_prompt_block_reason_in_diagnostics():
    """Prompt feedback block reasons should be visible without printing raw payloads."""
    response = _response(text="", finish_reason="SAFETY")
    response.prompt_feedback = SimpleNamespace(block_reason="SAFETY")
    provider = GeminiProvider(model="gemini-3.5-flash", api_key="k")
    provider._client.aio.models.generate_content = AsyncMock(return_value=response)

    result = await provider.send("system", "user")

    assert result.finish_reason == "SAFETY"
    assert result.diagnostics is not None
    assert result.diagnostics["block_reason"] == "SAFETY"
    assert result.diagnostics["empty_output_tokens"] is True


@pytest.mark.asyncio
async def test_stream_preserves_final_chunk_finish_diagnostics():
    """Streaming path should return the final candidate finish reason and usage too."""
    provider = GeminiProvider(model="gemini-3.5-flash", api_key="k")

    chunk = _response(text="", finish_reason="STOP")

    class _Iter:
        def __aiter__(self):
            async def gen():
                yield chunk

            return gen()

    stream = AsyncMock(return_value=_Iter())
    provider._client.aio.models.generate_content_stream = stream

    result = await provider.stream("system", "user")

    assert result.text == ""
    assert result.finish_reason == "STOP"
    assert result.usage == {"input_tokens": 15073, "output_tokens": 0}
    assert result.diagnostics is not None
    assert result.diagnostics["empty_output_tokens"] is True
