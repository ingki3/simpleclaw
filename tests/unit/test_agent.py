"""Tests for the agent orchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator


@pytest.fixture
def config_file(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"""
llm:
  default: "gemini"
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key_env: "GOOGLE_API_KEY"

agent:
  history_limit: 5
  db_path: "{tmp_path}/conversations.db"

skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: "AGENT.md"
      type: "agent"
""")
    # Create persona dir with AGENT.md
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text(
        "# Agent\n\nYou are SimpleClaw, a helpful assistant."
    )
    # Create empty skill dirs
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return config


class TestAgentOrchestrator:
    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_init(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        assert orchestrator._persona_prompt != ""
        assert "SimpleClaw" in orchestrator._persona_prompt

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_build_system_prompt(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        prompt = orchestrator._build_system_prompt()
        assert "SimpleClaw" in prompt

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_process_message(self, config_file):
        orchestrator = AgentOrchestrator(config_file)

        # Mock the router
        mock_response = MagicMock()
        mock_response.text = "Hello! I'm SimpleClaw."
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        response = await orchestrator.process_message("Hi", 123, 456)
        assert response == "Hello! I'm SimpleClaw."

        # Verify router was called with messages
        call_args = orchestrator._router.send.call_args[0][0]
        assert call_args.messages is not None
        assert len(call_args.messages) == 1
        assert call_args.messages[0]["role"] == "user"
        assert call_args.messages[0]["content"] == "Hi"

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_conversation_history(self, config_file):
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "Response"
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        # Send two messages
        await orchestrator.process_message("First", 123, 456)
        await orchestrator.process_message("Second", 123, 456)

        # Second call should include history
        second_call = orchestrator._router.send.call_args_list[1][0][0]
        assert len(second_call.messages) == 3  # user + assistant + user
        assert second_call.messages[0]["content"] == "First"
        assert second_call.messages[1]["role"] == "assistant"
        assert second_call.messages[2]["content"] == "Second"

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_history_limit(self, config_file):
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "OK"
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        # Send more messages than history_limit (5)
        for i in range(10):
            await orchestrator.process_message(f"Message {i}", 123, 456)

        # Last call should have limited history + current message
        last_call = orchestrator._router.send.call_args[0][0]
        # history_limit=5 means 5 recent messages from store + 1 current
        assert len(last_call.messages) <= 6

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_llm_error_handling(self, config_file):
        orchestrator = AgentOrchestrator(config_file)

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            side_effect=Exception("API error")
        )

        response = await orchestrator.process_message("Hi", 123, 456)
        assert "오류" in response

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_format_skills(self, config_file, tmp_path):
        orchestrator = AgentOrchestrator(config_file)

        # Create a mock skill
        mock_skill = MagicMock()
        mock_skill.name = "test-skill"
        mock_skill.description = "A test skill"
        mock_skill.skill_dir = str(tmp_path / "skills" / "test-skill")

        result = orchestrator._format_skills_for_prompt([mock_skill])
        assert "test-skill" in result
        assert "A test skill" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_format_skills_empty(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        assert orchestrator._format_skills_for_prompt([]) == ""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_default_system_prompt(self, config_file, tmp_path):
        # Create config without persona
        no_persona_dir = tmp_path / "empty_persona"
        no_persona_dir.mkdir()
        orchestrator = AgentOrchestrator(config_file)
        # With persona file, should use persona
        prompt = orchestrator._build_system_prompt()
        assert len(prompt) > 0
