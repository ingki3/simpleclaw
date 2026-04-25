"""Tests for Native Function Calling tool loop in AgentOrchestrator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import ToolCall


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
    orchestrator._skills_prompt = "## Available Skills\n\n- **weather-skill**: Check weather"

    return orchestrator


def _text_response(text: str, backend: str = "gemini") -> MagicMock:
    """Create a mock LLM response with text only (no tool calls)."""
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = None
    resp.backend_name = backend
    return resp


def _tool_response(
    tool_calls: list[ToolCall],
    text: str = "",
    backend: str = "gemini",
) -> MagicMock:
    """Create a mock LLM response with tool calls."""
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = tool_calls
    resp.backend_name = backend
    return resp


class TestMultiTurnExecution:
    """Tests for the Native Function Calling tool loop."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_single_turn_no_tool(self, config_file, tmp_path):
        """When LLM returns text without tool_calls, no tool is executed."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            return_value=_text_response("It's sunny today!")
        )

        result = await orchestrator.process_message("What's the weather?", 1, 1)
        assert result == "It's sunny today!"
        # Only 1 LLM call needed (text returned immediately)
        assert orchestrator._router.send.call_count == 1

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_multi_turn_two_tools(self, config_file, tmp_path):
        """LLM calls two tools sequentially then produces a final text answer."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: tool call (first tool)
        step1 = _tool_response([
            ToolCall(id="c1", name="execute_skill", arguments={
                "skill_name": "weather-skill", "command": "echo sunny",
            }),
        ])

        # Step 2: tool call (second tool)
        step2 = _tool_response([
            ToolCall(id="c2", name="execute_skill", arguments={
                "skill_name": "weather-skill", "command": "echo 25C",
            }),
        ])

        # Step 3: final text answer
        step3 = _text_response("Sunny, 25C.")

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
        action_resp = _tool_response([
            ToolCall(id="c1", name="execute_skill", arguments={
                "skill_name": "weather-skill", "command": "echo data",
            }),
        ])

        # Final response when max iterations hit (forced text-only call)
        final = _text_response("Partial result.")

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            side_effect=[action_resp, action_resp, action_resp, final]
        )

        result = await orchestrator.process_message("Complex query", 1, 1)
        assert result == "Partial result."
        # 3 tool iterations (max) + 1 forced final = 4
        assert orchestrator._router.send.call_count == 4

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_tool_result_messages_passed_to_router(self, config_file, tmp_path):
        """Tool results are included as tool messages in subsequent LLM calls."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: tool call
        step1 = _tool_response([
            ToolCall(id="c1", name="execute_skill", arguments={
                "skill_name": "weather-skill", "command": "echo first_result",
            }),
        ])

        # Step 2: final text answer
        step2 = _text_response("Done.")

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2])

        await orchestrator.process_message("Check weather", 1, 1)

        # Second call's LLMRequest should have messages containing tool result
        second_call_request = orchestrator._router.send.call_args_list[1][0][0]
        messages = second_call_request.messages

        # Find the tool result message
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "c1"
        assert tool_msgs[0]["name"] == "execute_skill"

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_no_skills_still_works(self, config_file):
        """When no skills are registered, tool loop still works (returns text)."""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            orchestrator = AgentOrchestrator(config_file)

        # No skills registered (default from config)
        assert len(orchestrator._skills) == 0

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            return_value=_text_response("No skills available.")
        )

        result = await orchestrator.process_message("Hi", 1, 1)
        assert result == "No skills available."
        assert orchestrator._router.send.call_count == 1

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dangerous_command_blocked_in_loop(self, config_file, tmp_path):
        """Dangerous commands are blocked even inside the tool loop."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: tool call with dangerous command
        step1 = _tool_response([
            ToolCall(id="c1", name="execute_skill", arguments={
                "skill_name": "weather-skill", "command": "rm -rf /",
            }),
        ])

        # Step 2: After blocked command, LLM sees blocked result and answers
        step2 = _text_response("Command was blocked.")

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2])

        result = await orchestrator.process_message("Delete everything", 1, 1)
        assert result == "Command was blocked."

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_tool_results_in_messages(self, config_file, tmp_path):
        """Tool results are included in the messages list for subsequent calls."""
        orchestrator = _make_orchestrator_with_skills(config_file, tmp_path)

        # Step 1: tool call
        step1 = _tool_response([
            ToolCall(id="c1", name="execute_skill", arguments={
                "skill_name": "weather-skill", "command": "echo weather_data",
            }),
        ])

        # Step 2: final text answer
        step2 = _text_response("The weather is great.")

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(side_effect=[step1, step2])

        await orchestrator.process_message("Weather?", 1, 1)

        # The second call should have messages with assistant (tool_calls) + tool result
        final_request = orchestrator._router.send.call_args_list[-1][0][0]
        messages = final_request.messages

        # Check assistant message with tool_calls
        assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["tool_calls"][0]["name"] == "execute_skill"

        # Check tool result message
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["name"] == "execute_skill"
        assert tool_msgs[0]["tool_call_id"] == "c1"
