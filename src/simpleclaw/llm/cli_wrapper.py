"""CLI tool subprocess wrapper as an LLM provider."""

from __future__ import annotations

import asyncio
import logging
import shutil

from simpleclaw.llm.models import (
    LLMCLINotFoundError,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
)
from simpleclaw.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class CLIProvider(LLMProvider):
    """Provider that wraps an external CLI tool as an LLM backend."""

    def __init__(
        self,
        command: str | None,
        args: list[str] | None = None,
        timeout: int = 120,
        name: str = "cli",
    ) -> None:
        if not command:
            raise LLMCLINotFoundError("No CLI command specified")
        self._command = command
        self._args = args or []
        self._timeout = timeout
        self._name = name

    async def send(
        self,
        system_prompt: str,
        user_message: str,
        messages: list[dict] | None = None,
    ) -> LLMResponse:
        # Check if the command exists
        if not shutil.which(self._command):
            raise LLMCLINotFoundError(
                f"CLI tool '{self._command}' not found on the system. "
                "Please install it first."
            )

        # Build the full command
        cmd_args = [self._command, *self._args]

        # Pass user message via stdin
        if messages is not None:
            parts = []
            if system_prompt:
                parts.append(f"System: {system_prompt}")
            for msg in messages:
                role = msg["role"].capitalize()
                parts.append(f"{role}: {msg['content']}")
            input_text = "\n\n".join(parts)
        elif system_prompt:
            input_text = f"System: {system_prompt}\n\nUser: {user_message}"
        else:
            input_text = user_message

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input_text.encode("utf-8")),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise LLMTimeoutError(
                f"CLI '{self._command}' timed out after {self._timeout}s"
            )
        except FileNotFoundError:
            raise LLMCLINotFoundError(
                f"CLI tool '{self._command}' not found on the system."
            )

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            raise LLMProviderError(
                f"CLI '{self._command}' exited with code {process.returncode}: "
                f"{stderr_text}"
            )

        response_text = stdout.decode("utf-8", errors="replace").strip()

        if not response_text:
            logger.warning("CLI '%s' returned empty response", self._command)

        return LLMResponse(
            text=response_text,
            backend_name=self._name,
            model=self._command,
        )
