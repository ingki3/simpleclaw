"""runtime_status 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.runtime_status import handle_runtime_status
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


def test_runtime_status_is_operator_scoped_only():
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

    assert "runtime_status" not in runtime_names
    assert "runtime_status" in operator_names


def test_runtime_status_collects_process_git_health_fd_and_redacts(tmp_path, monkeypatch):
    """mocked 프로세스/git/health 결과를 JSON으로 요약하고 secret을 마스킹한다."""
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("4242", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "daemon:\n"
        f"  pid_file: {pid_file}\n"
        "admin_api:\n"
        "  enabled: true\n"
        "  bind_host: 127.0.0.1\n"
        "  bind_port: 18082\n"
        "  token_secret: env:ADMIN_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token-value-1234567890")

    commands: list[list[str]] = []

    def runner(cmd: list[str]):
        commands.append(cmd)
        if cmd[:2] == ["ps", "-p"]:
            return _completed("4242 1 S 00:10 python scripts/run_bot.py --api-key ghp_ab...wxyz\n")
        if cmd[:3] == ["lsof", "-a", "-p"] and "cwd" in cmd:
            return _completed("p4242\nn/Users/simplist/.simpleclaw\n")
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _completed("/repo\n")
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return _completed("abc1234\n")
        if cmd[:2] == ["git", "branch"]:
            return _completed("feature/biz-371-runtime-status\n")
        if cmd and cmd[0] == "lsof" and "-iTCP" in cmd:
            return _completed("COMMAND PID NAME\nPython 4242 TCP 127.0.0.1:8082 (LISTEN)\n")
        if cmd[:2] == ["lsof", "-p"]:
            return _completed("COMMAND PID FD\nPython 4242 1u\nPython 4242 2u\n")
        if cmd[:2] == ["launchctl", "print"]:
            return _completed("service/com.simpleclaw.agent = { pid = 4242 }\n")
        return _completed(returncode=1)

    def urlopen(req, timeout):
        assert req.full_url == "http://127.0.0.1:18082/admin/v1/health"
        assert req.headers["Authorization"].startswith("Bearer ")
        return _FakeResponse(200, b'{"status":"ok","token":"should-redact"}')

    payload = json.loads(
        handle_runtime_status(
            {"include": ["process", "git", "health", "ports", "fd", "launchd"]},
            config_path=config,
            run_command=runner,
            urlopen=urlopen,
        )
    )

    assert payload["read_only"] is True
    assert payload["target_pid"] == 4242
    assert payload["process"]["alive"] is True
    assert payload["process"]["cwd"] == "/Users/simplist/.simpleclaw"
    assert payload["git"]["head"] == "abc1234"
    assert payload["health"]["status"] == 200
    assert payload["health"]["body"]["token"] == "[REDACTED]"
    assert payload["fd"]["count"] == 2
    assert any(cmd[:2] == ["ps", "-p"] for cmd in commands)
    serialized = json.dumps(payload)
    assert "secret-token-value" not in serialized
    assert "ghp_ab...wxyz" not in serialized


@pytest.mark.asyncio
async def test_orchestrator_runtime_status_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 runtime_status를 실행하지 않는다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="status-1", name="runtime_status", arguments={"include": ["scheduler"]})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed)["scheduler"]["available"] is False


@pytest.mark.asyncio
async def test_process_operator_message_exposes_runtime_status_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 runtime_status를 포함한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("상태 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "runtime_status" in {tool.name for tool in request.tools}
