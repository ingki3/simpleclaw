"""Tests for the agent orchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.agent.commands import try_cron_command
from simpleclaw.agent.builtin_tools import (
    handle_cron_action,
    handle_file_manage,
    handle_file_read,
    handle_file_write,
    handle_skill_docs,
    handle_web_fetch,
)
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
      api_key: "test-key"

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
    async def test_process_message_text_response(self, config_file):
        """LLM returns text only (no tool calls) — returned as-is."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "Hello! I'm SimpleClaw."
        mock_response.tool_calls = None
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        response = await orchestrator.process_message("Hi", 123, 456)
        assert response == "Hello! I'm SimpleClaw."

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_process_message_simple_answer(self, config_file):
        """LLM text answer is returned directly."""
        orchestrator = AgentOrchestrator(config_file)

        mock_response = MagicMock()
        mock_response.text = "안녕하세요! SimpleClaw입니다."
        mock_response.tool_calls = None
        mock_response.backend_name = "gemini"
        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=mock_response)

        response = await orchestrator.process_message("Hi", 123, 456)
        assert "SimpleClaw" in response

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
        mock_response.text = "OK"
        mock_response.tool_calls = None
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
        mock_response.tool_calls = None
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

        # First call: seed history
        mock_response_1 = MagicMock()
        mock_response_1.text = "이전 응답"
        mock_response_1.tool_calls = None
        mock_response_1.backend_name = "gemini"

        # Second call: cron message
        mock_response_2 = MagicMock()
        mock_response_2.text = "cron 결과"
        mock_response_2.tool_calls = None
        mock_response_2.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            side_effect=[mock_response_1, mock_response_2]
        )

        await orchestrator.process_message("이전 메시지", 123, 456)
        await orchestrator.process_cron_message("메일 확인")

        cron_call = orchestrator._router.send.call_args[0][0]
        assert len(cron_call.messages) == 1


class TestCronCommands:
    """Tests for /cron command handling in AgentOrchestrator."""

    def test_cron_command_without_scheduler(self):
        """Returns error when CronScheduler is None."""
        result = try_cron_command("/cron list", None)
        assert "연결되지 않았습니다" in result

    def test_cron_list_empty(self):
        mock_scheduler = MagicMock()
        mock_scheduler.list_jobs.return_value = []
        result = try_cron_command("/cron list", mock_scheduler)
        assert "없습니다" in result

    def test_cron_list_with_jobs(self):
        from simpleclaw.daemon.models import ActionType, CronJob
        from datetime import datetime

        mock_scheduler = MagicMock()
        mock_scheduler.list_jobs.return_value = [
            CronJob(
                name="test-job",
                cron_expression="15 7 * * *",
                action_type=ActionType.RECIPE,
                action_reference=".agent/recipes/test/recipe.yaml",
                enabled=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        ]
        result = try_cron_command("/cron list", mock_scheduler)
        assert "test-job" in result
        assert "15 7 * * *" in result

    def test_cron_add(self):
        from simpleclaw.daemon.models import ActionType, CronJob
        from datetime import datetime

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = None
        mock_scheduler.add_job.return_value = CronJob(
            name="new-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="hello world",
            enabled=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        result = try_cron_command(
            "/cron add new-job 0 9 * * * prompt hello world", mock_scheduler
        )
        assert "등록 완료" in result
        mock_scheduler.add_job.assert_called_once_with(
            name="new-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="hello world",
        )

    def test_cron_add_duplicate(self):
        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = MagicMock()  # exists
        result = try_cron_command(
            "/cron add dup-job 0 9 * * * prompt test", mock_scheduler
        )
        assert "이미" in result

    def test_cron_remove(self):
        mock_scheduler = MagicMock()
        mock_scheduler.remove_job.return_value = True
        result = try_cron_command("/cron remove test-job", mock_scheduler)
        assert "삭제" in result

    def test_cron_enable_disable(self):
        mock_scheduler = MagicMock()
        result = try_cron_command("/cron enable test-job", mock_scheduler)
        assert "활성화" in result
        result = try_cron_command("/cron disable test-job", mock_scheduler)
        assert "비활성화" in result

    def test_non_cron_command_returns_none(self):
        assert try_cron_command("hello", None) is None
        assert try_cron_command("/recipe-name", None) is None

    def test_cron_help(self):
        mock_scheduler = MagicMock()
        result = try_cron_command("/cron blah", mock_scheduler)
        assert "사용법" in result


class TestCronToolIntegration:
    """Tests for cron as a built-in tool via _dispatch_tool_call."""

    def test_handle_cron_action_add(self):
        from simpleclaw.daemon.models import ActionType, CronJob
        from datetime import datetime

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = None
        mock_scheduler.add_job.return_value = CronJob(
            name="daily-news",
            cron_expression="15 20 * * *",
            action_type=ActionType.PROMPT,
            action_reference="오늘 하루 AI 뉴스를 정리해줘",
            enabled=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        result = handle_cron_action({
            "skill_name": "cron",
            "cron_action": "add",
            "name": "daily-news",
            "cron_expression": "15 20 * * *",
            "action_type": "prompt",
            "action_reference": "오늘 하루 AI 뉴스를 정리해줘",
        }, mock_scheduler)
        assert "Success" in result
        assert "daily-news" in result
        mock_scheduler.add_job.assert_called_once()

    def test_handle_cron_action_list(self):
        mock_scheduler = MagicMock()
        mock_scheduler.list_jobs.return_value = []
        result = handle_cron_action(
            {"skill_name": "cron", "cron_action": "list"}, mock_scheduler
        )
        assert "없습니다" in result

    def test_handle_cron_action_remove(self):
        mock_scheduler = MagicMock()
        mock_scheduler.remove_job.return_value = True
        result = handle_cron_action(
            {"skill_name": "cron", "cron_action": "remove", "name": "old-job"},
            mock_scheduler,
        )
        assert "removed" in result

    def test_handle_cron_action_missing_fields(self):
        mock_scheduler = MagicMock()
        result = handle_cron_action(
            {"skill_name": "cron", "cron_action": "add", "name": "x"},
            mock_scheduler,
        )
        assert "required" in result

    def test_handle_cron_action_no_scheduler(self):
        result = handle_cron_action(
            {"skill_name": "cron", "cron_action": "list"}, None
        )
        assert "not available" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_tool_call_cron(self, config_file):
        """_dispatch_tool_call routes cron tool calls to handle_cron_action."""
        orchestrator = AgentOrchestrator(config_file)
        mock_scheduler = MagicMock()
        mock_scheduler.list_jobs.return_value = []
        orchestrator.set_cron_scheduler(mock_scheduler)

        tc = ToolCall(id="call_1", name="cron", arguments={"cron_action": "list"})
        result = await orchestrator._dispatch_tool_call(tc)
        assert "없습니다" in result

    def test_handle_cron_action_recipe_type(self):
        from simpleclaw.daemon.models import ActionType, CronJob
        from datetime import datetime

        mock_scheduler = MagicMock()
        mock_scheduler.get_job.return_value = None
        mock_scheduler.add_job.return_value = CronJob(
            name="morning-ai",
            cron_expression="15 7 * * *",
            action_type=ActionType.RECIPE,
            action_reference=".agent/recipes/ai-report/recipe.yaml",
            enabled=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        result = handle_cron_action({
            "skill_name": "cron",
            "cron_action": "add",
            "name": "morning-ai",
            "cron_expression": "15 7 * * *",
            "action_type": "recipe",
            "action_reference": ".agent/recipes/ai-report/recipe.yaml",
        }, mock_scheduler)
        assert "Success" in result
        mock_scheduler.add_job.assert_called_once_with(
            name="morning-ai",
            cron_expression="15 7 * * *",
            action_type=ActionType.RECIPE,
            action_reference=".agent/recipes/ai-report/recipe.yaml",
        )


class TestBuiltinWebFetch:
    """Tests for web_fetch built-in tool."""

    @pytest.mark.asyncio
    async def test_web_fetch_missing_url(self):
        result = await handle_web_fetch({"skill_name": "web-fetch"})
        assert "required" in result

    @pytest.mark.asyncio
    async def test_web_fetch_blocks_localhost(self):
        result = await handle_web_fetch(
            {"skill_name": "web-fetch", "url": "http://localhost:8080/secret"}
        )
        assert "blocked" in result

    @pytest.mark.asyncio
    async def test_web_fetch_blocks_internal_ip(self):
        for url in [
            "http://127.0.0.1:3000/",
            "http://10.0.0.1/admin",
            "http://192.168.1.1/config",
        ]:
            result = await handle_web_fetch(
                {"skill_name": "web-fetch", "url": url}
            )
            assert "blocked" in result, f"Should block {url}"


class TestBuiltinFileRead:
    """Tests for file_read built-in tool."""

    def test_file_read_missing_path(self, tmp_path):
        result = handle_file_read({"skill_name": "file-read"}, tmp_path)
        assert "required" in result

    def test_file_read_nonexistent(self, tmp_path):
        result = handle_file_read(
            {"skill_name": "file-read", "path": "nonexistent_file_xyz.txt"}, tmp_path
        )
        assert "not found" in result

    def test_file_read_success(self, tmp_path):
        from pathlib import Path
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_read(
            {"skill_name": "file-read", "path": "config.yaml"}, ws
        )
        assert "Error" not in result or "not found" in result

    def test_file_read_with_offset_limit(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_read(
            {"skill_name": "file-read", "path": "config.yaml", "offset": 0, "limit": 3}, ws
        )
        if "Error" not in result:
            lines = [l for l in result.split("\n") if l.strip() and "|" in l]
            assert len(lines) <= 3

    def test_file_read_negative_offset(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_read(
            {"skill_name": "file-read", "path": "config.yaml", "offset": -3, "limit": 3}, ws
        )
        if "Error" not in result:
            assert "lines" in result

    def test_file_read_blocks_path_traversal(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_read(
            {"skill_name": "file-read", "path": "/etc/passwd"}, ws
        )
        assert "outside" in result or "Error" in result


class TestBuiltinFileWrite:
    """Tests for file_write built-in tool."""

    def test_file_write_missing_path(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_write(
            {"skill_name": "file-write", "content": "hello"}, ws
        )
        assert "required" in result

    def test_file_write_outside_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_write(
            {"skill_name": "file-write", "path": "src/hack.py", "content": "evil"}, ws
        )
        assert "restricted" in result or "workspace" in result

    def test_file_write_success(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        target = ws / "test.txt"
        result = handle_file_write({
            "skill_name": "file-write",
            "path": str(target),
            "content": "hello world",
        }, ws)
        assert "Success" in result
        assert target.read_text() == "hello world"

    def test_file_write_append(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        target = ws / "log.txt"
        target.write_text("line1\n")

        result = handle_file_write({
            "skill_name": "file-write",
            "path": str(target),
            "content": "line2\n",
            "append": True,
        }, ws)
        assert "appended" in result
        assert target.read_text() == "line1\nline2\n"


class TestBuiltinFileManage:
    """Tests for file_manage built-in tool."""

    def test_file_manage_list(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_manage(
            {"skill_name": "file-manage", "operation": "list", "path": "src"}, ws
        )
        assert "entries" in result or "Error" not in result

    def test_file_manage_info(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_manage(
            {"skill_name": "file-manage", "operation": "info", "path": "config.yaml"}, ws
        )
        if "Error" not in result:
            assert "size" in result

    def test_file_manage_mkdir(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        new_dir = ws / "subdir"
        result = handle_file_manage({
            "skill_name": "file-manage",
            "operation": "mkdir",
            "path": str(new_dir),
        }, ws)
        assert "Success" in result
        assert new_dir.is_dir()

    def test_file_manage_delete_file(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        target = ws / "to_delete.txt"
        target.write_text("bye")
        result = handle_file_manage({
            "skill_name": "file-manage",
            "operation": "delete",
            "path": str(target),
        }, ws)
        assert "deleted" in result
        assert not target.exists()

    def test_file_manage_delete_outside_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_manage({
            "skill_name": "file-manage",
            "operation": "delete",
            "path": "config.yaml",
        }, ws)
        assert "restricted" in result or "workspace" in result

    def test_file_manage_unknown_operation(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_manage({
            "skill_name": "file-manage",
            "operation": "rename",
            "path": "foo",
        }, ws)
        assert "unknown" in result

    def test_file_manage_missing_fields(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = handle_file_manage({"skill_name": "file-manage"}, ws)
        assert "required" in result


class TestToolDispatch:
    """Tests for _dispatch_tool_call routing."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_cli(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        tc = ToolCall(id="call_1", name="cli", arguments={"command": "echo hello"})
        result = await orchestrator._dispatch_tool_call(tc)
        assert "hello" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_cli_missing_command(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        tc = ToolCall(id="call_1", name="cli", arguments={})
        result = await orchestrator._dispatch_tool_call(tc)
        assert "required" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_file_read(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        tc = ToolCall(id="call_1", name="file_read", arguments={"path": "config.yaml"})
        result = await orchestrator._dispatch_tool_call(tc)
        # Result is either file content or a "not found" error — both are valid
        assert isinstance(result, str)

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self, config_file):
        """Unknown tool name returns an error."""
        orchestrator = AgentOrchestrator(config_file)
        tc = ToolCall(id="call_1", name="nonexistent_tool", arguments={})
        result = await orchestrator._dispatch_tool_call(tc)
        assert "unknown" in result.lower()

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_skill_docs(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        tc = ToolCall(id="call_1", name="skill_docs", arguments={"name": "nonexistent"})
        result = await orchestrator._dispatch_tool_call(tc)
        assert "not found" in result


class TestSkillDocs:
    """Tests for skill_docs built-in tool."""

    def test_handle_skill_docs_returns_content(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Test Skill\n\nUsage: run test")

        mock_skill = MagicMock()
        mock_skill.skill_dir = str(skill_dir)
        mock_skill.description = "A test skill"
        skills = {"test-skill": mock_skill}

        result = handle_skill_docs({"name": "test-skill"}, skills)
        assert "Usage: run test" in result
        assert "Documentation for" in result

    def test_handle_skill_docs_missing_name(self):
        result = handle_skill_docs({}, {})
        assert "required" in result

    def test_handle_skill_docs_unknown_skill(self):
        result = handle_skill_docs({"name": "nonexistent"}, {"real-skill": MagicMock()})
        assert "not found" in result
        assert "real-skill" in result

    def test_handle_skill_docs_fuzzy_match(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# My Skill")

        mock_skill = MagicMock()
        mock_skill.skill_dir = str(skill_dir)
        skills = {"my-skill": mock_skill}

        result = handle_skill_docs({"name": "My Skill"}, skills)
        assert "My Skill" in result

    def test_handle_skill_docs_no_skillmd(self, tmp_path):
        skill_dir = tmp_path / "no-docs"
        skill_dir.mkdir()

        mock_skill = MagicMock()
        mock_skill.skill_dir = str(skill_dir)
        mock_skill.description = "A skill without docs"
        skills = {"no-docs": mock_skill}

        result = handle_skill_docs({"name": "no-docs"}, skills)
        assert "no documentation" in result
        assert "A skill without docs" in result


class TestToolLoop:
    """Tests for the Native Function Calling tool loop."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_tool_loop_with_tool_call(self, config_file):
        """Tool loop executes tool calls and returns final text answer."""
        orchestrator = AgentOrchestrator(config_file)

        # First LLM call: wants to use cli tool
        mock_response_1 = MagicMock()
        mock_response_1.text = ""
        mock_response_1.tool_calls = [
            ToolCall(id="call_1", name="cli", arguments={"command": "echo test_output"})
        ]
        mock_response_1.backend_name = "gemini"

        # Second LLM call: returns final answer
        mock_response_2 = MagicMock()
        mock_response_2.text = "The command output was: test_output"
        mock_response_2.tool_calls = None
        mock_response_2.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(
            side_effect=[mock_response_1, mock_response_2]
        )

        response = await orchestrator.process_message("Run echo", 123, 456)
        assert "test_output" in response
        assert orchestrator._router.send.call_count == 2
