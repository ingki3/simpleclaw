"""외부 CLI 도구를 LLM 프로바이더로 감싸는 서브프로세스 래퍼.

로컬에 설치된 CLI LLM 도구(예: llama.cpp, ollama 등)를
LLMProvider 인터페이스로 통합하여 라우터에서 API 프로바이더와
동일하게 사용할 수 있게 한다.

동작 흐름:
  1. shutil.which()로 CLI 바이너리 존재 여부 확인
  2. 시스템/사용자 메시지를 텍스트로 조합하여 stdin으로 전달
  3. 타임아웃 내에 stdout 응답을 수집하여 LLMResponse로 반환
"""

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
    """외부 CLI 도구를 LLM 백엔드로 감싸는 프로바이더."""

    def __init__(
        self,
        command: str | None,
        args: list[str] | None = None,
        timeout: int = 120,
        name: str = "cli",
    ) -> None:
        """CLIProvider를 초기화한다.

        Args:
            command: 실행할 CLI 바이너리 이름 또는 경로.
            args: CLI에 전달할 추가 인자 리스트.
            timeout: 프로세스 최대 실행 시간(초). 초과 시 TimeoutError 발생.
            name: 라우터에서 이 백엔드를 식별하는 이름.
        """
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
        """CLI 도구에 메시지를 stdin으로 전달하고 stdout 응답을 반환한다."""
        # 실행 전 바이너리 존재 여부를 확인하여 명확한 에러 메시지 제공
        if not shutil.which(self._command):
            raise LLMCLINotFoundError(
                f"CLI tool '{self._command}' not found on the system. "
                "Please install it first."
            )

        cmd_args = [self._command, *self._args]

        # 멀티턴 대화를 단일 텍스트로 직렬화하여 stdin에 전달
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
