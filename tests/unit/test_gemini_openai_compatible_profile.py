"""Gemini OpenAI-compatible profile contract tests."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.turn_analysis import TURN_ANALYSIS_RESPONSE_SCHEMA
from simpleclaw.llm.models import (
    LLMConfigError,
    LLMRequest,
    MultimodalAttachment,
    ToolDefinition,
)
from simpleclaw.llm.profiles import get_provider_profile
from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.router import create_router


def _contains_key(node: object, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def test_gemini_openai_profile_uses_openai_chat_transport():
    profile = get_provider_profile("gemini-openai")

    assert profile.default_transport == "openai_chat"
    assert profile.name == "gemini-openai"
    assert profile.capabilities.structured_output is True
    assert profile.capabilities.tools is False
    assert profile.capabilities.multimodal is False
    assert profile.capabilities.native_replay is False
    assert profile.capabilities.reasoning is False


def test_gemini_openai_schema_removes_native_ordering_without_mutating_source():
    source = copy.deepcopy(TURN_ANALYSIS_RESPONSE_SCHEMA)

    adapted = get_provider_profile("gemini-openai").adapt_schema(source)

    assert not _contains_key(adapted, "propertyOrdering")
    assert adapted["additionalProperties"] is False
    assert TURN_ANALYSIS_RESPONSE_SCHEMA == source
    assert "propertyOrdering" in TURN_ANALYSIS_RESPONSE_SCHEMA


def test_gemini_openai_backend_shares_openai_provider_transport(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  routes:
    default: {primary: gemini_openai_ab}
  providers:
    gemini_openai_ab:
      type: api
      model: gemini-2.5-flash
      transport: openai_chat
      profile: gemini-openai
      api_key: test-key
      base_url: https://generativelanguage.googleapis.com/v1beta/openai/
""".strip(),
        encoding="utf-8",
    )

    router = create_router(config)

    assert isinstance(router._providers["gemini_openai_ab"], OpenAIProvider)
    assert router.get_backend_profile("gemini_openai_ab").name == "gemini-openai"


@pytest.mark.asyncio
async def test_gemini_openai_tool_request_is_rejected_before_provider_call(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  routes:
    default: {primary: gemini_openai_ab}
  providers:
    gemini_openai_ab:
      type: api
      model: gemini-2.5-flash
      transport: openai_chat
      profile: gemini-openai
      api_key: test-key
      base_url: https://generativelanguage.googleapis.com/v1beta/openai/
""".strip(),
        encoding="utf-8",
    )
    router = create_router(config)
    provider = router._providers["gemini_openai_ab"]
    provider.send = AsyncMock()

    with pytest.raises(LLMConfigError, match="tools"):
        await router.send(
            LLMRequest(
                user_message="Use the tool.",
                tools=[
                    ToolDefinition(
                        name="lookup",
                        description="Lookup a value.",
                        parameters={"type": "object", "properties": {}},
                    )
                ],
            )
        )

    provider.send.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_openai_attachment_is_rejected_before_silent_payload_drop(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  routes:
    default: {primary: gemini_openai_ab}
  providers:
    gemini_openai_ab:
      type: api
      model: gemini-2.5-flash
      transport: openai_chat
      profile: gemini-openai
      api_key: test-key
      base_url: https://generativelanguage.googleapis.com/v1beta/openai/
""".strip(),
        encoding="utf-8",
    )
    router = create_router(config)
    provider = router._providers["gemini_openai_ab"]
    provider.send = AsyncMock()

    with pytest.raises(LLMConfigError, match="multimodal"):
        await router.send(
            LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": "Describe this image.",
                        "attachments": [
                            MultimodalAttachment(
                                data=b"image-bytes", mime_type="image/png"
                            )
                        ],
                    }
                ]
            )
        )

    provider.send.assert_not_called()
