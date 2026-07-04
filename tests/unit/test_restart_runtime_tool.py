"""restart_runtime 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.restart_runtime import handle_restart_runtime
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall


@dataclass
class _FakeResponse:
    """urllib response context manager 대체용."""

    status: int
    body: bytes

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        """Admin API health body bytes를 반환한다."""
        return self.body


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """subprocess.CompletedProcess 테스트 값을 만든다."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _write_config(tmp_path, *, pid="111"):
    """restart_runtime 테스트용 live config와 pid 파일을 작성한다."""
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(pid, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "daemon:\n"
        f"  pid_file: {pid_file}\n"
        "admin_api:\n"
        "  enabled: true\n"
        "  bind_host: 127.0.0.1\n"
        "  bind_port: 18082\n"
        "  token_secret: env:ADMIN_TOKEN\n"
        "telegram:\n"
        "  bot_token: env:TELEGRAM_BOT_TOKEN\n"
        "  streaming:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    return config, pid_file


def test_restart_runtime_is_operator_scoped_only():
    """기본 runtime build에는 보이지 않고 operator gate가 열릴 때만 노출된다."""
    runtime_names = {tool.name for tool in build_tool_definitions(skills=[])}
    operator_names = {
        tool.name
        for tool in build_tool_definitions(
            skills=[],
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR),
            operator_gate=True,
        )
    }

    assert "restart_runtime" not in runtime_names
    assert "restart_runtime" in operator_names


def test_restart_runtime_requires_explicit_confirmation(tmp_path):
    """confirm=False면 launchctl을 실행하지 않고 operator 승인 필요 메시지를 반환한다."""
    config, _ = _write_config(tmp_path)
    commands: list[list[str]] = []

    payload = json.loads(
        handle_restart_runtime(
            {"method": "launchagent_kickstart", "confirm": False, "reason": "test"},
            config_path=config,
            run_command=lambda cmd: commands.append(cmd) or _completed(),
            cwd=tmp_path,
        )
    )

    assert payload["ok"] is False
    assert payload["approval_required"] is True
    assert "confirm=true" in payload["error"]
    assert commands == []


def test_restart_runtime_kickstarts_launchagent_and_returns_post_health(tmp_path, monkeypatch):
    """kickstart 후 PID 변경, Admin health, scheduler/dashboard/telegram/FD 상태를 반환한다."""
    config, pid_file = _write_config(tmp_path)
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:telegram-secret-token-abcdef")
    commands: list[list[str]] = []

    def runner(cmd: list[str]):
        commands.append(cmd)
        if cmd[:2] == ["launchctl", "kickstart"]:
            pid_file.write_text("222", encoding="utf-8")
            return _completed("service restarted\n")
        if cmd[:2] == ["ps", "-p"]:
            return _completed("222 1 S 00:01 python scripts/run_bot.py --token ghp_live_secret_1234567890\n")
        if cmd[:2] == ["lsof", "-p"]:
            return _completed("COMMAND PID FD\nPython 222 1u\nPython 222 2u\nPython 222 3u\n")
        return _completed(returncode=1, stderr="unexpected command")

    def urlopen(req, timeout):
        assert req.full_url == "http://127.0.0.1:18082/admin/v1/health"
        assert req.headers["Authorization"].startswith("Bearer ")
        return _FakeResponse(
            200,
            b'{"status":"ok","telegram":{"enabled":true},"scheduler":{"running":true},"dashboard":{"registered":true},"token":"leak-me"}',
        )

    scheduler = MagicMock()
    scheduler.running = True
    payload = json.loads(
        handle_restart_runtime(
            {"method": "launchagent_kickstart", "confirm": True, "reason": "operator requested"},
            config_path=config,
            scheduler=scheduler,
            run_command=runner,
            urlopen=urlopen,
            sleep=lambda _seconds: None,
            cwd=tmp_path,
        )
    )

    assert payload["ok"] is True
    assert payload["method"] == "launchagent_kickstart"
    assert payload["cwd_check"]["ok"] is True
    assert payload["launchctl"]["returncode"] == 0
    assert payload["pid"]["before"] == 111
    assert payload["pid"]["after"] == 222
    assert payload["pid"]["changed"] is True
    assert payload["health"]["ok"] is True
    assert payload["post_checks"]["telegram"]["configured"] is True
    assert payload["post_checks"]["scheduler"]["running"] is True
    assert payload["post_checks"]["dashboard"]["registered"] is True
    assert payload["post_checks"]["fd"]["count"] == 3
    assert any(cmd[:2] == ["launchctl", "kickstart"] for cmd in commands)
    serialized = json.dumps(payload)
    assert "admin-secret-token" not in serialized
    assert "telegram-secret-token" not in serialized
    assert "ghp_live_secret" not in serialized
    assert "leak-me" not in serialized


def test_restart_runtime_reports_post_health_failure(tmp_path):
    """kickstart가 성공해도 post-health 실패와 PID 미변경은 log_debug로 이어갈 수 있게 표시한다."""
    config, _ = _write_config(tmp_path)

    def runner(cmd: list[str]):
        if cmd[:2] == ["launchctl", "kickstart"]:
            return _completed("ok\n")
        if cmd[:2] == ["ps", "-p"]:
            return _completed("111 1 S 00:01 python scripts/run_bot.py\n")
        if cmd[:2] == ["lsof", "-p"]:
            return _completed("", "permission denied", 1)
        return _completed(returncode=1)

    def failing_urlopen(req, timeout):
        raise OSError("connection refused")

    payload = json.loads(
        handle_restart_runtime(
            {"method": "launchagent_kickstart", "confirm": True, "reason": "health fail test"},
            config_path=config,
            run_command=runner,
            urlopen=failing_urlopen,
            sleep=lambda _seconds: None,
            cwd=tmp_path,
        )
    )

    assert payload["ok"] is False
    assert payload["launchctl"]["ok"] is True
    assert payload["pid"]["changed"] is False
    assert payload["health"]["ok"] is False
    assert "connection refused" in payload["health"]["error"]
    assert "log_debug" in payload["next_step"]


@pytest.mark.asyncio
async def test_orchestrator_restart_runtime_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 restart_runtime을 실행하지 않는다."""
    config, _ = _write_config(tmp_path)
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_restart_runtime",
        lambda args, **kwargs: json.dumps({"ok": True, "reason": args["reason"]}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(
        id="restart-1",
        name="restart_runtime",
        arguments={"method": "launchagent_kickstart", "confirm": True, "reason": "operator"},
    )

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "reason": "operator"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_restart_runtime_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 restart_runtime을 포함한다."""
    config, _ = _write_config(tmp_path)
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("명시 승인 후 런타임 재시작")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "restart_runtime" in {tool.name for tool in request.tools}