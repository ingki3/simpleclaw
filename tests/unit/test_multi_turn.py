"""Tests for ReAct loop execution in AgentOrchestrator."""

import json
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
  max_tool_iterations: 3

security:
  command_guard:
    enabled: true
    allowlist: []
  env_passthrough: []

skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
  execution_timeout: 30

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: "AGENT.md"
      type: "agent"
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return config


def _make_orchestrator_with_skills(config_file, tmp_path):
    """Create an orchestrator with a fake skill registered."""
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
        orchestrator = AgentOrchestrator(config_file)

    # Register a fake skill
    mock_skill = MagicMock()
    mock_skill.name = "weather-skill"
    mock_skill.description = "Check weather"
    mock_skill.trigger = "weather, 날씨"
    mock_skill.skill_dir = str(tmp_path)
    mock_skill.script_path = None

    orchestrator._skills = [mock_skill]
    orchestrator._skills_by_name = {"weather-skill": mock_skill}
    orchestrator._skills_router_list = "- weather-skill: Check weather"
    orchestrator._skills_router_list_with_usage = "### weather-skill\nCheck weather"

    return orchestrator


class TestMultiTurnExecution:
    """Tests for the ReAct loop."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_single_turn_backward_compat(self, config_file, tmp_path):
        """When LLM returns Answer directly, no tool is executed."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # LLM returns an Answer directly (no tool needed)
        react_response = MagicMock()
        react_response.text = "Thought: The user just wants a greeting.\nAnswer: It's sunny today!"
        react_response.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=react_response)

        result = await orchestrator.process_message("What's the weather?", 1, 1)
        assert result == "It's sunny today!"
        # Only 1 ReAct step needed (Answer returned immediately)
        assert orchestrator._router.send.call_count == 1

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_multi_turn_two_tools(self, config_file, tmp_path):
        """LLM calls two tools then produces an Answer."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: Action (first tool)
        step1 = MagicMock()
        step1.text = (
            'Thought: I need to check the weather.\n'
            'Action: {"skill_name": "weather-skill", "command": "echo sunny"}'
        )
        step1.backend_name = "gemini"

        # Step 2: Action (second tool)
        step2 = MagicMock()
        step2.text = (
            'Thought: Now I need the temperature.\n'
            'Action: {"skill_name": "weather-skill", "command": "echo 25C"}'
        )
        step2.backend_name = "gemini"

        # Step 3: Answer
        step3 = MagicMock()
        step3.text = "Thought: I have all the info.\nAnswer: Sunny, 25C."
        step3.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2, step3])

        result = await orchestrator.process_message("Weather and temp?", 1, 1)
        assert result == "Sunny, 25C."
        assert orchestrator._router.send.call_count == 3

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_max_iterations_limit(self, config_file, tmp_path):
        """Loop stops after max_tool_iterations even if LLM keeps requesting tools."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)
        assert orchestrator._max_tool_iterations == 3

        # LLM always wants more tools
        action_resp = MagicMock()
        action_resp.text = (
            'Thought: Need more data.\n'
            'Action: {"skill_name": "weather-skill", "command": "echo data"}'
        )
        action_resp.backend_name = "gemini"

        # Final response when max iterations hit (_generate_final)
        final = MagicMock()
        final.text = "Partial result."
        final.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            side_effect=[action_resp, action_resp, action_resp, final]
        )

        result = await orchestrator.process_message("Complex query", 1, 1)
        assert result == "Partial result."
        # 3 ReAct steps (max iterations) + 1 _generate_final = 4
        assert orchestrator._router.send.call_count == 4

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_tool_context_passed_to_router(self, config_file, tmp_path):
        """Previous tool results (Observations) are included in subsequent ReAct steps."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: Action
        step1 = MagicMock()
        step1.text = (
            'Thought: I need weather info.\n'
            'Action: {"skill_name": "weather-skill", "command": "echo first_result"}'
        )
        step1.backend_name = "gemini"

        # Step 2: Answer
        step2 = MagicMock()
        step2.text = "Thought: Got the data.\nAnswer: Done."
        step2.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2])

        await orchestrator.process_message("Check weather", 1, 1)

        # Second ReAct step should contain the trace with Observation
        second_call = orchestrator._router.send.call_args_list[1][0][0]
        assert "Observation:" in second_call.user_message

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_no_skills_skips_loop(self, config_file):
        """When no skills are registered, ReAct still works (returns Answer)."""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            orchestrator = AgentOrchestrator(config_file)

        # No skills registered (default from config)
        assert len(orchestrator._skills) == 0

        final = MagicMock()
        final.text = "Answer: No skills available."
        final.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=final)

        result = await orchestrator.process_message("Hi", 1, 1)
        assert result == "No skills available."
        assert orchestrator._router.send.call_count == 1

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dangerous_command_blocked_in_loop(self, config_file, tmp_path):
        """Dangerous commands are blocked even inside the ReAct loop."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: Action with dangerous command
        step1 = MagicMock()
        step1.text = (
            'Thought: I need to clean up.\n'
            'Action: {"skill_name": "weather-skill", "command": "rm -rf /"}'
        )
        step1.backend_name = "gemini"

        # Step 2: After blocked command, LLM sees blocked observation and answers
        step2 = MagicMock()
        step2.text = "Thought: The command was blocked.\nAnswer: Command was blocked."
        step2.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2])

        result = await orchestrator.process_message("Delete everything", 1, 1)
        assert result == "Command was blocked."

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_tool_results_in_final_response(self, config_file, tmp_path):
        """Tool results (Observations) are included in subsequent ReAct step context."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: Action
        step1 = MagicMock()
        step1.text = (
            'Thought: I need weather data.\n'
            'Action: {"skill_name": "weather-skill", "command": "echo weather_data"}'
        )
        step1.backend_name = "gemini"

        # Step 2: Answer
        step2 = MagicMock()
        step2.text = "Thought: Got the weather data.\nAnswer: The weather is great."
        step2.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2])

        await orchestrator.process_message("Weather?", 1, 1)

        # The second call should have the Observation in the user_message (trace)
        final_call = orchestrator._router.send.call_args_list[-1][0][0]
        assert "Observation:" in final_call.user_message
        assert "weather-skill" in final_call.user_message
