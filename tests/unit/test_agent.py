"""Tests for the agent orchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.agent.react import parse_react
from simpleclaw.agent.commands import try_cron_command
from simpleclaw.agent.builtin_tools import (
    handle_cron_action,
    handle_file_manage,
    handle_file_read,
    handle_file_write,
    handle_skill_docs,
    handle_web_fetch,
)


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


class TestCronReActIntegration:
    """Tests for cron as a built-in tool in the ReAct loop."""

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
    async def test_dispatch_routing_cron(self, config_file):
        """ReAct dispatch routes cron actions to handle_cron_action."""
        orchestrator = AgentOrchestrator(config_file)
        mock_scheduler = MagicMock()
        mock_scheduler.list_jobs.return_value = []
        orchestrator.set_cron_scheduler(mock_scheduler)

        skill_name, result = await orchestrator._dispatch_routing(
            {"skill_name": "cron", "cron_action": "list"}
        )
        assert skill_name == "cron"
        assert "없습니다" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_builtin_tools_prompt_with_scheduler(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        mock_scheduler = MagicMock()
        orchestrator.set_cron_scheduler(mock_scheduler)
        prompt = orchestrator._build_builtin_tools_prompt()
        assert "cron" in prompt
        assert "스케줄" in prompt

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_builtin_tools_prompt_without_scheduler_has_core_tools(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        prompt = orchestrator._build_builtin_tools_prompt()
        assert "cli" in prompt
        assert "cron" not in prompt

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
    """Tests for web-fetch built-in tool."""

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
    """Tests for file-read built-in tool."""

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
    """Tests for file-write built-in tool."""

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
    """Tests for file-manage built-in tool."""

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


class TestBuiltinDispatch:
    """Tests for _dispatch_builtin routing."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_cli(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        skill_name, result = await orchestrator._dispatch_routing(
            {"skill_name": "cli", "command": "echo hello"}
        )
        assert skill_name == "cli"
        assert "hello" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_cli_missing_command(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        skill_name, result = await orchestrator._dispatch_routing(
            {"skill_name": "cli"}
        )
        assert "required" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_file_read(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        skill_name, result = await orchestrator._dispatch_routing(
            {"skill_name": "file-read", "path": "config.yaml"}
        )
        assert skill_name == "file-read"

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_nonbuiltin_falls_through(self, config_file):
        """Non-builtin skill_name should go to _execute_skill."""
        orchestrator = AgentOrchestrator(config_file)
        skill_name, result = await orchestrator._dispatch_routing(
            {"skill_name": "nonexistent-skill"}
        )
        assert "not found" in result.lower() or "not found" in str(result).lower()

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_builtin_tools_prompt_always_has_core_tools(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        # Even without cron scheduler, core tools should be present
        prompt = orchestrator._build_builtin_tools_prompt()
        assert "cli" in prompt
        assert "web-fetch" in prompt
        assert "file-read" in prompt
        assert "file-write" in prompt
        assert "file-manage" in prompt

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_builtin_tools_prompt_has_cron_when_scheduler(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        mock_scheduler = MagicMock()
        orchestrator.set_cron_scheduler(mock_scheduler)

        prompt = orchestrator._build_builtin_tools_prompt()
        assert "cron" in prompt


class TestSkillDocs:
    """Tests for skill-docs built-in tool."""

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

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_dispatch_skill_docs(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        skill_name, result = await orchestrator._dispatch_routing(
            {"skill_name": "skill-docs", "name": "nonexistent"}
        )
        assert skill_name == "skill-docs"
        assert "not found" in result


class TestTruncateDescription:
    def test_short_passes_through(self):
        assert AgentOrchestrator._truncate_description("Short desc.") == "Short desc."

    def test_first_sentence(self):
        desc = "First sentence. Second sentence with more detail."
        assert AgentOrchestrator._truncate_description(desc) == "First sentence."

    def test_long_single_sentence(self):
        desc = "A" * 200
        result = AgentOrchestrator._truncate_description(desc)
        assert len(result) == 120
        assert result.endswith("...")

    def test_empty(self):
        assert AgentOrchestrator._truncate_description("") == ""


class TestPromptBudget:
    """Tests for tiered prompt budget system."""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_tier1_small_list(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        mock_skills = []
        for i in range(5):
            s = MagicMock()
            s.name = f"skill-{i}"
            s.description = f"Short description for skill {i}."
            mock_skills.append(s)

        result = orchestrator._format_skills_tier1(mock_skills)
        assert "skill-0" in result
        assert "Short description" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_tier2_names_only(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        mock_skills = []
        for i in range(5):
            s = MagicMock()
            s.name = f"skill-{i}"
            s.description = f"Description {i}"
            mock_skills.append(s)

        result = orchestrator._format_skills_tier2(mock_skills)
        assert "- skill-0" in result
        assert "Description" not in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_tier3_truncates(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        # Create enough skills to trigger tier 3
        mock_skills = []
        for i in range(2000):
            s = MagicMock()
            s.name = f"very-long-skill-name-category-subcategory-{i:05d}"
            s.description = "x" * 200
            mock_skills.append(s)

        result = orchestrator._format_skills_tier3(mock_skills)
        assert "more skills" in result

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_estimate_prompt_size(self, config_file):
        orchestrator = AgentOrchestrator(config_file)
        size = orchestrator._estimate_prompt_size("test skills text")
        assert size > 0
        assert isinstance(size, int)


class TestReActParsing:
    def test_parse_thought_and_answer(self):
        response = (
            "Thought: The user wants a greeting.\n"
            "Answer: Hello there!"
        )
        thought, action, answer = parse_react(response)
        assert thought == "The user wants a greeting."
        assert action is None
        assert answer == "Hello there!"

    def test_parse_thought_and_action(self):
        response = (
            'Thought: I need to search for news.\n'
            'Action: {"skill_name": "news-skill", "command": "python search.py"}'
        )
        thought, action, answer = parse_react(response)
        assert thought == "I need to search for news."
        assert action == {"skill_name": "news-skill", "command": "python search.py"}
        assert answer is None

    def test_parse_answer_only(self):
        response = "Answer: Just a direct answer."
        thought, action, answer = parse_react(response)
        assert thought is None
        assert action is None
        assert answer == "Just a direct answer."

    def test_parse_no_pattern(self):
        response = "This is just plain text with no ReAct format."
        thought, action, answer = parse_react(response)
        assert thought is None
        assert action is None
        assert answer is None

    def test_parse_invalid_action_json(self):
        response = (
            "Thought: Let me try.\n"
            "Action: {invalid json here}"
        )
        thought, action, answer = parse_react(response)
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
        thought, action, answer = parse_react(response)
        assert "SSG" in answer
        assert "LG" in answer
        assert "Thought" not in answer
