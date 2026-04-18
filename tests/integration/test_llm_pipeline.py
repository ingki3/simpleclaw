"""Integration test: persona engine + LLM router pipeline."""

from unittest.mock import AsyncMock

import pytest

from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.llm.models import LLMRequest, LLMResponse
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter


class RecordingProvider(LLMProvider):
    """Provider that records calls for testing."""

    def __init__(self):
        self.last_system_prompt = None
        self.last_user_message = None

    async def send(self, system_prompt: str, user_message: str, messages: list[dict] | None = None) -> LLMResponse:
        self.last_system_prompt = system_prompt
        self.last_user_message = user_message
        return LLMResponse(
            text="Mock response",
            backend_name="test",
            model="test-model",
        )


@pytest.fixture
def persona_workspace(tmp_path):
    """Create persona files for integration testing."""
    local = tmp_path / ".agent"
    local.mkdir()
    (local / "AGENT.md").write_text(
        "# Agent\n\nI am SimpleClaw agent.", encoding="utf-8"
    )
    (local / "USER.md").write_text(
        "# User\n\nUser prefers Korean.", encoding="utf-8"
    )
    return local


class TestPersonaLLMPipeline:
    @pytest.mark.asyncio
    async def test_persona_prompt_injected_into_llm(self, persona_workspace, tmp_path):
        """Full pipeline: resolve persona -> assemble -> send to LLM."""
        # Step 1: Resolve and assemble persona
        files = resolve_persona_files(persona_workspace, tmp_path / "no_global")
        assembly = assemble_prompt(files, token_budget=4096)

        assert "SimpleClaw" in assembly.assembled_text
        assert "Korean" in assembly.assembled_text

        # Step 2: Create router with recording provider
        recorder = RecordingProvider()
        router = LLMRouter(
            backends={},
            providers={"test": recorder},
            default_backend="test",
        )

        # Step 3: Send request with persona as system prompt
        request = LLMRequest(
            system_prompt=assembly.assembled_text,
            user_message="Hello",
        )
        response = await router.send(request)

        # Step 4: Verify
        assert response.text == "Mock response"
        assert recorder.last_system_prompt == assembly.assembled_text
        assert "SimpleClaw" in recorder.last_system_prompt
        assert recorder.last_user_message == "Hello"

    @pytest.mark.asyncio
    async def test_no_persona_still_works(self, tmp_path):
        """LLM works without persona files."""
        files = resolve_persona_files(tmp_path / "no_local", tmp_path / "no_global")
        assembly = assemble_prompt(files, token_budget=4096)

        recorder = RecordingProvider()
        router = LLMRouter(
            backends={},
            providers={"test": recorder},
            default_backend="test",
        )

        request = LLMRequest(
            system_prompt=assembly.assembled_text,
            user_message="Hello",
        )
        response = await router.send(request)

        assert response.text == "Mock response"
        assert recorder.last_system_prompt == ""
