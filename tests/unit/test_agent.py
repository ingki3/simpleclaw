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
  max_tool_iterations: 5

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
    async def test_process_message_fallback(self, config_file):
        """Plain text LLM response (no ReAct format) is returned as-is."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "Hello! I'm SimpleClaw."
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        response = await orchestrator.process_message("Hi", 123, 456)
        assert response == "Hello! I'm SimpleClaw."

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_process_message_react_answer(self, config_file):
        """ReAct Answer format is parsed and returned."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = (
            "Thought: The user wants a greeting. No tool needed.\n"
            "Answer: 안녕하세요! SimpleClaw입니다."
        )
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        response = await orchestrator.process_message("Hi", 123, 456)
        assert "SimpleClaw" in response
        assert "Thought:" not in response

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
    @pytest.mark.asyncio
    async def test_conversation_stored(self, config_file):
        """Messages are stored in conversation DB."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "Answer: OK"
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        await orchestrator.process_message("Hello", 123, 456)
        assert orchestrator._store.count() == 2  # user + assistant

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_format_skills(self, config_file, tmp_path):
        orchestrator = AgentOrchestrator(config_file)

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
        no_persona_dir = tmp_path / "empty_persona"
        no_persona_dir.mkdir()
        orchestrator = AgentOrchestrator(config_file)
        prompt = orchestrator._build_system_prompt()
        assert len(prompt) > 0

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_process_cron_message_not_stored(self, config_file):
        """Cron messages must NOT be stored in the shared conversation DB."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "[NO_NOTIFY]"
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        count_before = orchestrator._store.count()
        await orchestrator.process_cron_message("읽지 않은 메일 확인")
        count_after = orchestrator._store.count()

        assert count_after == count_before

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_process_cron_message_isolated(self, config_file):
        """Cron messages should use isolated context (no history)."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "Answer: 결과입니다."
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        # Seed history
        mock_response.text = "이전 응답"
        await orchestrator.process_message("이전 메시지", 123, 456)

        # Cron call should have only 1 message (no history)
        mock_response.text = "Answer: cron 결과"
        await orchestrator.process_cron_message("메일 확인")

        cron_call = orchestrator._router.send.call_args[0][0]
        assert len(cron_call.messages) == 1


class TestReActParsing:
    def test_parse_thought_and_answer(self):
        response = (
            "Thought: The user wants a greeting.\n"
            "Answer: Hello there!"
        )
        thought, action, answer = AgentOrchestrator._parse_react(response)
        assert thought == "The user wants a greeting."
        assert action is None
        assert answer == "Hello there!"

    def test_parse_thought_and_action(self):
        response = (
            'Thought: I need to search for news.\n'
            'Action: {"skill_name": "news-skill", "command": "python search.py"}'
        )
        thought, action, answer = AgentOrchestrator._parse_react(response)
        assert thought == "I need to search for news."
        assert action == {"skill_name": "news-skill", "command": "python search.py"}
        assert answer is None

    def test_parse_answer_only(self):
        response = "Answer: Just a direct answer."
        thought, action, answer = AgentOrchestrator._parse_react(response)
        assert thought is None
        assert action is None
        assert answer == "Just a direct answer."

    def test_parse_no_pattern(self):
        response = "This is just plain text with no ReAct format."
        thought, action, answer = AgentOrchestrator._parse_react(response)
        assert thought is None
        assert action is None
        assert answer is None

    def test_parse_invalid_action_json(self):
        response = (
            "Thought: Let me try.\n"
            "Action: {invalid json here}"
        )
        thought, action, answer = AgentOrchestrator._parse_react(response)
        assert thought == "Let me try."
        assert action is None
        assert answer is None

    def test_parse_multiline_answer(self):
        response = (
            "Thought: All data collected.\n"
            "Answer: 오늘 결과입니다:\n"
            "- SSG 5:0 kt\n"
            "- LG 4:1 두산"
        )
        thought, action, answer = AgentOrchestrator._parse_react(response)
        assert "SSG" in answer
        assert "LG" in answer
        assert "Thought" not in answer
