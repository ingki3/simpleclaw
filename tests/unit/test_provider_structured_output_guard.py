"""BIZ-430 — 미지원 provider 의 required structured output 공통 가드 테스트.

BIZ-427 의 공통 가드(``LLMProvider._reject_required_structured_output``)는
mime/schema 힌트가 있을 때만 거부했다. BIZ-430 부터 "required" 계약은 힌트
유무가 아니라 호출자의 보장 요구(``require_structured_output=True``)만으로
결정된다 — CLI/Claude/OpenAI 처럼 schema-constrained 출력을 보장할 수 없는
provider 는 힌트 없는 required 요청도 즉시 ``LLMProviderError`` 로 거부해야
한다. ``require_structured_output=False`` 인 기존 호출(힌트 포함)은 계속
조용히 무시되어 회귀가 없어야 한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.llm.cli_wrapper import CLIProvider
from simpleclaw.llm.models import LLMProviderError
from simpleclaw.llm.providers.claude import ClaudeProvider
from simpleclaw.llm.providers.openai_provider import OpenAIProvider

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


# ---------------------------------------------------------------------------
# CLIProvider
# ---------------------------------------------------------------------------


class TestCLIProviderStructuredOutputGuard:
    @pytest.mark.asyncio
    async def test_send_rejects_required_without_hints(self):
        """required 단독 요청(힌트 없음)도 subprocess 실행 전에 거부해야 한다."""
        provider = CLIProvider(command="cat", name="cli")

        with pytest.raises(LLMProviderError, match="cli"):
            await provider.send("", "hello", require_structured_output=True)

    @pytest.mark.asyncio
    async def test_send_not_required_ignores_hints(self):
        """require=False 면 힌트는 조용히 무시되고 기존 호출이 그대로 동작한다."""
        provider = CLIProvider(command="cat", args=[], timeout=10, name="cli")

        result = await provider.send(
            "",
            "Hello CLI",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=False,
        )

        assert "Hello CLI" in result.text


# ---------------------------------------------------------------------------
# ClaudeProvider
# ---------------------------------------------------------------------------


def _build_claude_provider() -> ClaudeProvider:
    provider = ClaudeProvider(model="claude-sonnet-4-20250514", api_key="test-key")
    mock_message = MagicMock()
    mock_message.content = [MagicMock(type="text", text="ok")]
    mock_message.usage = MagicMock(input_tokens=1, output_tokens=1)
    provider._client.messages.create = AsyncMock(return_value=mock_message)
    return provider


class TestClaudeProviderStructuredOutputGuard:
    @pytest.mark.asyncio
    async def test_send_rejects_required_without_hints(self):
        provider = _build_claude_provider()

        with pytest.raises(LLMProviderError, match="claude"):
            await provider.send("sys", "hello", require_structured_output=True)
        # 가드는 API 호출 전에 실패해야 한다.
        provider._client.messages.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stream_rejects_required_without_hints(self):
        provider = _build_claude_provider()

        with pytest.raises(LLMProviderError, match="claude"):
            await provider.stream("sys", "hello", require_structured_output=True)

    @pytest.mark.asyncio
    async def test_send_not_required_ignores_hints(self):
        provider = _build_claude_provider()

        result = await provider.send(
            "sys",
            "hello",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=False,
        )

        assert result.text == "ok"


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


def _build_openai_provider() -> OpenAIProvider:
    provider = OpenAIProvider(model="gpt-4o", api_key="test-key")
    mock_choice = MagicMock()
    mock_choice.message.content = "ok"
    mock_choice.message.tool_calls = None
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    provider._client.chat.completions.create = AsyncMock(return_value=mock_response)
    return provider


class TestOpenAIProviderStructuredOutputGuard:
    @pytest.mark.asyncio
    async def test_send_rejects_required_without_hints(self):
        provider = _build_openai_provider()

        with pytest.raises(LLMProviderError, match="openai"):
            await provider.send("sys", "hello", require_structured_output=True)
        provider._client.chat.completions.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stream_rejects_required_without_hints(self):
        provider = _build_openai_provider()

        with pytest.raises(LLMProviderError, match="openai"):
            await provider.stream("sys", "hello", require_structured_output=True)

    @pytest.mark.asyncio
    async def test_send_not_required_ignores_hints(self):
        provider = _build_openai_provider()

        result = await provider.send(
            "sys",
            "hello",
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            require_structured_output=False,
        )

        assert result.text == "ok"
