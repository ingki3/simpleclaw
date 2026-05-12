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
        # 메트릭은 기본적으로 None이어야 기존 호환성을 유지한다.
        assert orchestrator._metrics is None

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    def test_init_accepts_metrics(self, config_file):
        """``metrics`` 인자가 주입되면 오케스트레이터에 보존되어야 한다."""
        from simpleclaw.logging.metrics import MetricsCollector

        metrics = MetricsCollector()
        orchestrator = AgentOrchestrator(config_file, metrics=metrics)
        assert orchestrator._metrics is metrics

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

    @pytest.mark.asyncio
    async def test_web_fetch_short_static_falls_back_to_headless(self):
        """정적 본문이 임계값 미만이면 자동으로 headless 경로로 폴백."""
        from simpleclaw.agent import builtin_tools

        static_mock = AsyncMock(return_value="tiny")  # 4 chars < 200 threshold
        headless_mock = AsyncMock(return_value="full rendered article body")

        with patch.object(builtin_tools, "_fetch_static", static_mock), \
             patch.object(builtin_tools, "_fetch_headless", headless_mock):
            result = await handle_web_fetch(
                {"url": "https://example.com/spa"}
            )

        static_mock.assert_awaited_once_with("https://example.com/spa")
        headless_mock.assert_awaited_once_with(
            "https://example.com/spa", headless_binary=None
        )
        assert "via headless render" in result
        assert "static fetch returned 4 chars" in result
        assert "full rendered article body" in result

    @pytest.mark.asyncio
    async def test_web_fetch_long_static_skips_headless(self):
        """정적 본문이 임계값 이상이면 정적 결과를 그대로 반환 — headless 호출 없음."""
        from simpleclaw.agent import builtin_tools

        long_body = "x" * 500
        static_mock = AsyncMock(return_value=long_body)
        headless_mock = AsyncMock(return_value="should not be called")

        with patch.object(builtin_tools, "_fetch_static", static_mock), \
             patch.object(builtin_tools, "_fetch_headless", headless_mock):
            result = await handle_web_fetch(
                {"url": "https://example.com/article"}
            )

        static_mock.assert_awaited_once()
        headless_mock.assert_not_awaited()
        assert result == long_body
        assert "headless" not in result

    @pytest.mark.asyncio
    async def test_web_fetch_force_headless_skips_static(self):
        """force_headless=True 면 정적 경로를 호출하지 않고 곧바로 headless."""
        from simpleclaw.agent import builtin_tools

        static_mock = AsyncMock(return_value="should not be called")
        headless_mock = AsyncMock(return_value="rendered content")

        with patch.object(builtin_tools, "_fetch_static", static_mock), \
             patch.object(builtin_tools, "_fetch_headless", headless_mock):
            result = await handle_web_fetch(
                {"url": "https://example.com/spa", "force_headless": True}
            )

        static_mock.assert_not_awaited()
        headless_mock.assert_awaited_once_with(
            "https://example.com/spa", headless_binary=None
        )
        assert "force_headless=True" in result
        assert "rendered content" in result

    @pytest.mark.asyncio
    async def test_web_fetch_static_error_does_not_fall_back(self):
        """정적 fetch 가 HTTP 오류를 반환하면 headless 폴백을 시도하지 않는다."""
        from simpleclaw.agent import builtin_tools

        static_mock = AsyncMock(return_value="Error: HTTP 404 — Not Found")
        headless_mock = AsyncMock(return_value="should not be called")

        with patch.object(builtin_tools, "_fetch_static", static_mock), \
             patch.object(builtin_tools, "_fetch_headless", headless_mock):
            result = await handle_web_fetch(
                {"url": "https://example.com/missing"}
            )

        static_mock.assert_awaited_once()
        headless_mock.assert_not_awaited()
        assert "Error: HTTP 404" in result

    @pytest.mark.asyncio
    async def test_web_fetch_headless_failure_returns_static_body(self):
        """headless 폴백이 실패하면 LLM 이 문맥을 잃지 않도록 정적 본문이라도 반환."""
        from simpleclaw.agent import builtin_tools

        static_mock = AsyncMock(return_value="tiny")
        headless_mock = AsyncMock(
            return_value="Error: headless fallback unavailable — 'agent-browser' CLI not found in PATH."
        )

        with patch.object(builtin_tools, "_fetch_static", static_mock), \
             patch.object(builtin_tools, "_fetch_headless", headless_mock):
            result = await handle_web_fetch(
                {"url": "https://example.com/spa"}
            )

        assert "headless fallback failed" in result
        assert "tiny" in result


class TestResolveAgentBrowser:
    """BIZ-162 — `_resolve_agent_browser` CLI 탐색 다단계 강건성."""

    def test_config_override_takes_priority(self, tmp_path):
        """config override 가 PATH/glob 보다 우선해 사용된다."""
        from simpleclaw.agent.builtin_tools import _resolve_agent_browser

        # 실행 가능한 스텁 바이너리 생성
        binary = tmp_path / "agent-browser-custom"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)

        # PATH 에 동명의 다른 바이너리가 있어도 override 가 이긴다.
        with patch("simpleclaw.agent.builtin_tools.shutil.which",
                   return_value="/usr/local/bin/agent-browser"):
            resolved, searched = _resolve_agent_browser(override=str(binary))

        assert resolved == str(binary)
        assert any("config override" in s for s in searched)

    def test_path_lookup_when_no_override(self):
        """override 없으면 ``shutil.which`` 결과를 사용한다."""
        from simpleclaw.agent.builtin_tools import _resolve_agent_browser

        with patch("simpleclaw.agent.builtin_tools.shutil.which",
                   return_value="/path/from/which/agent-browser"):
            resolved, searched = _resolve_agent_browser(override=None)

        assert resolved == "/path/from/which/agent-browser"
        assert any("$PATH" in s for s in searched)

    def test_glob_candidate_used_when_path_missing(self, tmp_path):
        """PATH 가 비어 있어도 알려진 후보 glob 이 매치하면 그것을 사용한다.

        nohup 데몬에서 fnm shim 디렉터리가 PATH 에서 빠진 회귀 시나리오 (BIZ-162).
        """
        from simpleclaw.agent import builtin_tools
        from simpleclaw.agent.builtin_tools import _resolve_agent_browser

        # tmp_path 안에 npm npx 캐시 모양의 실행 가능한 스텁을 만든다.
        npx_dir = tmp_path / "npx_cache" / "abcd1234" / "node_modules" / "agent-browser" / "bin"
        npx_dir.mkdir(parents=True)
        binary = npx_dir / "agent-browser-darwin-arm64"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)

        fake_candidates = (
            str(tmp_path / "npx_cache" / "*" / "node_modules"
                / "agent-browser" / "bin" / "agent-browser-darwin-arm64"),
        )
        with patch("simpleclaw.agent.builtin_tools.shutil.which",
                   return_value=None), \
             patch.object(builtin_tools, "_AGENT_BROWSER_GLOB_CANDIDATES",
                          fake_candidates):
            resolved, searched = _resolve_agent_browser(override=None)

        assert resolved == str(binary)
        assert any(str(tmp_path) in s for s in searched)

    def test_all_paths_fail_returns_none_with_diagnostic(self):
        """탐색 모두 실패하면 None + searched 목록을 반환해 진단 메시지에 동봉 가능."""
        from simpleclaw.agent import builtin_tools
        from simpleclaw.agent.builtin_tools import _resolve_agent_browser

        with patch("simpleclaw.agent.builtin_tools.shutil.which",
                   return_value=None), \
             patch.object(builtin_tools, "_AGENT_BROWSER_GLOB_CANDIDATES",
                          ("/nonexistent/agent-browser",)):
            resolved, searched = _resolve_agent_browser(override=None)

        assert resolved is None
        # 진단에 PATH 와 glob 후보가 모두 박혀 있어야 운영자가 즉시 원인 파악 가능.
        assert any("$PATH" in s for s in searched)
        assert any("/nonexistent/agent-browser" in s for s in searched)


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
        mock_skill.name = "test-skill"
        mock_skill.skill_dir = str(skill_dir)
        mock_skill.description = "A test skill"
        skills = {"test-skill": mock_skill}

        result = handle_skill_docs({"name": "test-skill"}, skills)
        assert "Usage: run test" in result
        assert "Documentation for" in result

    def test_handle_skill_docs_prepends_invocation_header(self, tmp_path):
        """BIZ-166: 응답 첫 부분에 execute_skill 호출 형식 + uvx 금지 안내가 들어간다."""
        skill_dir = tmp_path / "news-search-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "# News Search\n\nSome usage text far from the top."
        )

        mock_skill = MagicMock()
        mock_skill.name = "news-search-skill"
        mock_skill.skill_dir = str(skill_dir)
        mock_skill.description = "search news"
        skills = {"news-search-skill": mock_skill}

        result = handle_skill_docs({"name": "news-search-skill"}, skills)

        # 호출 형식이 본문보다 먼저 나와야 모델이 도입부만 읽고도 학습 가능
        header_index = result.find("execute_skill(skill_name=")
        body_index = result.find("# News Search")
        assert header_index != -1, f"invocation header 누락: {result[:200]}"
        assert body_index != -1
        assert header_index < body_index, (
            "invocation header 가 SKILL.md 본문보다 앞에 있어야 함"
        )
        assert "uvx news-search-skill" in result
        assert "pipx run news-search-skill" in result

    def test_handle_skill_docs_no_skillmd_still_has_header(self, tmp_path):
        """SKILL.md 가 없어도 invocation header 만큼은 반환한다."""
        skill_dir = tmp_path / "no-docs-skill"
        skill_dir.mkdir()

        mock_skill = MagicMock()
        mock_skill.name = "no-docs-skill"
        mock_skill.skill_dir = str(skill_dir)
        mock_skill.description = "skill without SKILL.md"
        skills = {"no-docs-skill": mock_skill}

        result = handle_skill_docs({"name": "no-docs-skill"}, skills)
        assert 'execute_skill(skill_name="no-docs-skill"' in result
        assert "uvx no-docs-skill" in result
        assert "no documentation" in result

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

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @pytest.mark.asyncio
    async def test_tool_loop_strips_past_tool_traces_from_history(
        self, config_file
    ):
        """BIZ-164 회귀 — 과거 턴의 ``role=tool`` 메시지와 assistant ``tool_calls`` 필드는
        새 사용자 메시지 처리 시 LLM 입력에서 잘려야 한다.

        2026-05-12 17:46 "오늘 롯데 선발투수 누구지?" 사고의 패턴: 5/10 의 옛 대화에서
        시도했던 ``link-git-summarizer`` 스킬 흔적이 history 에 남아 작은 모델이 새
        메시지에서도 같은 도구를 재시도하다가 max-iter 까지 낭비. 필터가 적용되면
        다음 LLM 호출의 ``messages`` 어디에도 ``link-git-summarizer`` 가 등장하지
        않아야 한다.
        """
        import json
        from types import SimpleNamespace

        orchestrator = AgentOrchestrator(config_file)

        # 5/10 의 실패 도구 호출 흔적 — 향후 store 가 tool 역할/`tool_calls` 를
        # 적재하더라도 필터가 잡아야 한다. 현재 enum 에 없는 형태를 직접 시뮬레이션.
        stale_tool_msg = SimpleNamespace(
            role=SimpleNamespace(value="tool"),
            content=(
                "Tool result: ls /Users/simplist/Dev/skills/link-git-summarizer "
                "→ No such file or directory"
            ),
        )
        stale_assistant_msg = SimpleNamespace(
            role=SimpleNamespace(value="assistant"),
            content="확인해보겠습니다.",
            tool_calls=[{
                "id": "call_old",
                "name": "link-git-summarizer",
                "arguments": {"url": "https://example.com"},
            }],
        )

        orchestrator._store = MagicMock()
        orchestrator._store.get_recent = MagicMock(
            return_value=[stale_assistant_msg, stale_tool_msg]
        )
        orchestrator._store.add_message = MagicMock(return_value=1)

        # 새 메시지 처리 시 LLM 은 텍스트만 반환(도구 호출 없이 즉시 답변).
        final_response = MagicMock()
        final_response.text = "오늘 롯데 선발투수는 박세웅입니다."
        final_response.tool_calls = None
        final_response.backend_name = "gemini"

        orchestrator._router = MagicMock()
        orchestrator._router.send = AsyncMock(return_value=final_response)

        await orchestrator.process_message(
            "오늘 롯데 선발투수 누구지?", 123, 456
        )

        request = orchestrator._router.send.call_args[0][0]

        # role=tool 메시지는 history 에서 사라져야 한다.
        assert all(m.get("role") != "tool" for m in request.messages), (
            f"role=tool history leaked into LLM messages: {request.messages}"
        )
        # 과거 assistant 메시지의 tool_calls 구조 필드는 messages 에 부착되지
        # 않아야 한다 (현재 턴 내부 in-flight 만 허용 — 본 시나리오에선 tool_calls 가
        # 한 번도 발생하지 않으므로 어떤 메시지에도 키가 없어야 한다).
        assert all("tool_calls" not in m for m in request.messages), (
            f"tool_calls field leaked from history: {request.messages}"
        )
        # 핵심 회귀 단언: 5/10 의 스킬 이름이 LLM 입력 어디에도 등장하지 않아야 한다.
        serialized = json.dumps(request.messages, ensure_ascii=False)
        assert "link-git-summarizer" not in serialized, (
            f"stale skill name leaked into LLM messages: {serialized}"
        )

    def test_tool_usage_instruction_contains_failed_skill_guard(self):
        """BIZ-164 #3 — system prompt 가드가 ``_TOOL_USAGE_INSTRUCTION`` 에 박혀 있어야 한다."""
        from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION

        assert "fail in a prior turn" in _TOOL_USAGE_INSTRUCTION, (
            "BIZ-164 prompt guard missing — 과거 실패 도구 재시도 가드 한 줄이 누락됨"
        )
