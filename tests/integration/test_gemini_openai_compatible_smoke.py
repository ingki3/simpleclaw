"""Credential-gated Gemini OpenAI-compatible A/B parity matrix.

The native Gemini transport remains the production path.  These smoke tests
are intentionally opt-in because they call a billable external endpoint.  Any
result is evidence for a future profile change only after it is recorded and
the provider-neutral request conversion is reviewed.  In particular, the
image case uses the endpoint's documented OpenAI content shape directly;
SimpleClaw attachments remain preflight-blocked until that conversion exists.
"""

from __future__ import annotations

import os

import pytest

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.llm.models import ToolDefinition
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.openai_provider import OpenAIProvider


@pytest.fixture
def gemini_openai_config() -> dict[str, str]:
    if os.getenv("SIMPLECLAW_RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("set SIMPLECLAW_RUN_LIVE_LLM_TESTS=1 to run billable live LLM tests")
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    model = os.getenv("SIMPLECLAW_GEMINI_OPENAI_MODEL")
    if not api_key or not model:
        pytest.skip("GOOGLE_API_KEY/GEMINI_API_KEY and SIMPLECLAW_GEMINI_OPENAI_MODEL are required")
    return {"api_key": api_key, "model": model}


def _provider(
    config: dict[str, str], *, extra_body: dict | None = None
) -> OpenAIProvider:
    return OpenAIProvider(
        model=config["model"],
        api_key=config["api_key"],
        name="gemini_openai_ab",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        extra_body=extra_body,
        profile=get_provider_profile("gemini-openai"),
    )


@pytest.fixture
def gemini_openai_provider(
    gemini_openai_config: dict[str, str],
) -> OpenAIProvider:
    return _provider(gemini_openai_config)


@pytest.mark.asyncio
async def test_text_response(gemini_openai_provider: OpenAIProvider):
    response = await gemini_openai_provider.send(
        system_prompt="Reply with exactly: OK",
        user_message="Reply now.",
        max_tokens=32,
    )

    assert response.text.strip()


@pytest.mark.asyncio
async def test_turn_analysis_json_schema(gemini_openai_provider: OpenAIProvider):
    response = await gemini_openai_provider.send(
        system_prompt="Return a valid object matching the requested schema.",
        user_message="Analyze this short request: hello",
        response_mime_type="application/json",
        response_schema=TURN_ANALYSIS_RESPONSE_SCHEMA,
        require_structured_output=True,
        max_tokens=1024,
    )

    assert response.text.strip().startswith("{")


@pytest.mark.asyncio
async def test_tool_round_trip_and_replay_matrix(
    gemini_openai_provider: OpenAIProvider,
):
    response = await gemini_openai_provider.send(
        system_prompt="Use the supplied tool for this request.",
        user_message="Call get_weather for Seoul, then wait for the result.",
        tools=[
            ToolDefinition(
                name="get_weather",
                description="Return a weather summary for a city.",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            )
        ],
        max_tokens=128,
    )

    assert response.tool_calls, "endpoint did not return the requested tool call"
    tool_call = response.tool_calls[0]
    final_response = await gemini_openai_provider.send(
        system_prompt="Summarize the supplied tool result in one sentence.",
        user_message="",
        messages=[
            {"role": "user", "content": "What is the weather in Seoul?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": "Seoul is clear and 20C.",
            },
        ],
        max_tokens=128,
    )

    assert final_response.text.strip()


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning_effort", ["none", "medium"])
async def test_reasoning_effort_matrix(
    gemini_openai_config: dict[str, str], reasoning_effort: str
):
    provider = _provider(
        gemini_openai_config,
        extra_body={"reasoning_effort": reasoning_effort},
    )

    response = await provider.send(
        system_prompt="Reply with exactly: OK",
        user_message="Reply now.",
        max_tokens=32,
    )

    assert response.text.strip()


@pytest.mark.asyncio
async def test_image_input_matrix(gemini_openai_provider: OpenAIProvider):
    # One transparent 1×1 PNG.  This deliberately uses the documented
    # OpenAI-compatible content shape, not SimpleClaw's unsupported
    # ``attachments`` field.
    image_data = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A"
        "AusB9Y9J7aAAAAAASUVORK5CYII="
    )
    response = await gemini_openai_provider.send(
        system_prompt="Answer briefly about the image input.",
        user_message="",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Can you see this image?"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_data}"
                        },
                    },
                ],
            }
        ],
        max_tokens=64,
    )

    assert response.text.strip()
