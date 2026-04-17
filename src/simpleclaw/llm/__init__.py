"""LLM interface module (stub).

This module will be fully implemented in Phase 1 Task 2
(다중 LLM API 연동 및 외부 CLI 툴 서브프로세스 래핑).
"""

from __future__ import annotations


class LLMClient:
    """Placeholder LLM client interface.

    Accepts a system prompt (from PromptAssembly.assembled_text)
    and user messages. To be implemented with actual LLM API
    integration in the next development phase.
    """

    def send_message(
        self,
        system_prompt: str,
        user_message: str,
    ) -> str:
        """Send a message to the LLM with the given system prompt.

        Args:
            system_prompt: The assembled persona system prompt.
            user_message: The user's input message.

        Returns:
            The LLM's response text.

        Raises:
            NotImplementedError: Always, until Phase 1 Task 2 is complete.
        """
        raise NotImplementedError(
            "LLM client not yet implemented. "
            "See Phase 1 Task 2: 다중 LLM API 연동."
        )
