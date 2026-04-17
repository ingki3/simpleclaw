"""Tests for the CLI wrapper provider."""

import pytest

from simpleclaw.llm.cli_wrapper import CLIProvider
from simpleclaw.llm.models import (
    LLMCLINotFoundError,
    LLMProviderError,
    LLMTimeoutError,
)


class TestCLIProvider:
    def test_missing_command_raises(self):
        with pytest.raises(LLMCLINotFoundError):
            CLIProvider(command=None)

    @pytest.mark.asyncio
    async def test_successful_echo(self):
        """Test CLI wrapper with echo command."""
        provider = CLIProvider(
            command="cat",  # cat reads stdin and echoes it
            args=[],
            timeout=10,
            name="test-cli",
        )
        result = await provider.send("", "Hello CLI")
        assert "Hello CLI" in result.text
        assert result.backend_name == "test-cli"

    @pytest.mark.asyncio
    async def test_cli_not_found(self):
        provider = CLIProvider(
            command="nonexistent_tool_xyz",
            args=[],
            name="bad-cli",
        )
        with pytest.raises(LLMCLINotFoundError):
            await provider.send("", "hello")

    @pytest.mark.asyncio
    async def test_timeout(self):
        provider = CLIProvider(
            command="sleep",
            args=["10"],
            timeout=1,
            name="slow-cli",
        )
        with pytest.raises(LLMTimeoutError):
            await provider.send("", "hello")

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self):
        provider = CLIProvider(
            command="bash",
            args=["-c", "echo error >&2; exit 1"],
            timeout=10,
            name="fail-cli",
        )
        with pytest.raises(LLMProviderError, match="exited with code 1"):
            await provider.send("", "hello")
