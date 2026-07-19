"""Credential-gated Gemini OpenAI-compatible A/B parity matrix.

The native Gemini transport remains the production path.  These smoke tests
are intentionally opt-in because they call a billable external endpoint.  Any
unsupported feature stays an explicit XFAIL until a recorded provider result
justifies a profile capability change.
"""

from __future__ import annotations

import os

import pytest

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.openai_provider import OpenAIProvider

@pytest.fixture
def gemini_openai_provider() -> OpenAIProvider:
    if os.getenv("SIMPLECLAW_RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("set SIMPLECLAW_RUN_LIVE_LLM_TESTS=1 to run billable live LLM tests")
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    model = os.getenv("SIMPLECLAW_GEMINI_OPENAI_MODEL")
    if not api_key or not model:
        pytest.skip("GOOGLE_API_KEY/GEMINI_API_KEY and SIMPLECLAW_GEMINI_OPENAI_MODEL are required")
    return OpenAIProvider(
        model=model,
        api_key=api_key,
        name="gemini_openai_ab",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        profile=get_provider_profile("gemini-openai"),
    )


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


@pytest.mark.xfail(
    reason="Gemini OpenAI-compatible tool/replay parity is unverified; native route remains required.",
    strict=False,
)
def test_tool_round_trip_and_replay_metadata_are_not_claimed_yet():
    pytest.fail("Run and record the live tool-call → tool-result → final-response matrix first")


@pytest.mark.xfail(
    reason="Gemini OpenAI-compatible reasoning controls are unverified; native thinking remains required.",
    strict=False,
)
def test_reasoning_off_and_medium_are_not_claimed_yet():
    pytest.fail("Run and record the live reasoning off/medium matrix first")


@pytest.mark.xfail(
    reason="Gemini OpenAI-compatible image attachment parity is unverified; native multimodal route remains required.",
    strict=False,
)
def test_image_attachment_is_not_claimed_yet():
    pytest.fail("Run and record the live image attachment matrix first")
