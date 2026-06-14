"""deploy_status 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.deploy_status import handle_deploy_status
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """subprocess.CompletedProcess 테스트 값을 만든다."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_deploy_status_is_operator_scoped_only():
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

    assert "deploy_status" not in runtime_names
    assert "deploy_status" in operator_names


def test_deploy_status_summarizes_git_sync_dirty_overlap_and_prs():
    """git/gh mock 결과로 origin sync, dirty overlap, deploy/dev range, PR 목록을 요약한다."""
    commands: list[list[str]] = []

    def runner(cmd: list[str]):
        commands.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _completed("/repo\n")
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return _completed("main\n")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return _completed("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return _completed("aaaaaaa\n")
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _completed("origin/main\n")
        if cmd[:3] == ["git", "rev-list", "--left-right"] and cmd[-1] == "HEAD...origin/main":
            return _completed("2\t3\n")
        if cmd[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed(" M src/simpleclaw/agent/tool_schemas.py\n?? notes.txt\n")
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _completed("src/simpleclaw/agent/tool_schemas.py\nREADME.md\n")
        if cmd[:4] == ["git", "log", "--oneline", "--no-decorate"] and cmd[-1] == "origin/main..HEAD":
            return _completed("aaaaaaa local deploy fix\nbbbbbbb older local\n")
        if cmd[:4] == ["git", "log", "--oneline", "--no-decorate"] and cmd[-1] == "origin/main..origin/dev":
            return _completed("ddddddd dev unreleased\n")
        if cmd[:3] == ["gh", "pr", "list"]:
            return _completed(
                json.dumps([
                    {
                        "number": 12,
                        "title": "Release dev",
                        "url": "https://github.com/example/repo/pull/12",
                        "headRefName": "dev",
                        "baseRefName": "main",
                        "state": "OPEN",
                    }
                ])
            )
        return _completed(returncode=1, stderr=f"unexpected command: {' '.join(cmd)}")

    payload = json.loads(
        handle_deploy_status(
            {"compare": "main", "include_prs": True},
            run_command=runner,
        )
    )

    assert payload["read_only"] is True
    assert payload["compare"] == "main"
    assert payload["repo"]["root"] == "/repo"
    assert payload["repo"]["branch"] == "main"
    assert payload["origin_sync"] == {
        "target": "origin/main",
        "ahead": 2,
        "behind": 3,
        "status": "diverged",
    }
    assert payload["dirty"]["clean"] is False
    assert payload["dirty"]["paths"] == [
        {
            "path": "src/simpleclaw/agent/tool_schemas.py",
            "status": "M",
            "overlaps_deploy_range": True,
        },
        {"path": "notes.txt", "status": "??", "overlaps_deploy_range": False},
    ]
    assert payload["deploy_range"]["commits"][0] == {
        "sha": "aaaaaaa",
        "subject": "local deploy fix",
    }
    assert payload["unreleased_dev"]["commits"] == [
        {"sha": "ddddddd", "subject": "dev unreleased"}
    ]
    assert payload["open_prs"][0]["number"] == 12
    assert any(cmd[:3] == ["gh", "pr", "list"] for cmd in commands)


def test_deploy_status_gracefully_falls_back_without_gh():
    """gh가 없거나 실패해도 git-only summary는 ok로 반환한다."""

    def runner(cmd: list[str]):
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return _completed("/repo\n")
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return _completed("main\n")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return _completed("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return _completed("aaaaaaa\n")
        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return _completed("origin/main\n")
        if cmd[:3] == ["git", "rev-list", "--left-right"]:
            return _completed("0\t0\n")
        if cmd[:3] == ["git", "status", "--porcelain=v1"]:
            return _completed("")
        if cmd[:3] == ["git", "diff", "--name-only"]:
            return _completed("")
        if cmd[:4] == ["git", "log", "--oneline", "--no-decorate"]:
            return _completed("")
        if cmd[:3] == ["gh", "pr", "list"]:
            return _completed(returncode=127, stderr="gh: command not found")
        return _completed(returncode=1)

    payload = json.loads(handle_deploy_status({"include_prs": True}, run_command=runner))

    assert payload["ok"] is True
    assert payload["dirty"]["clean"] is True
    assert payload["origin_sync"]["status"] == "in_sync"
    assert payload["open_prs"] == []
    assert payload["gh"] == {"available": False, "error": "gh: command not found"}


@pytest.mark.asyncio
async def test_orchestrator_deploy_status_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 deploy_status를 실행하지 않는다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_deploy_status",
        lambda args: json.dumps({"ok": True, "compare": args.get("compare", "main")}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="deploy-1", name="deploy_status", arguments={"compare": "dev"})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "compare": "dev"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_deploy_status_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 deploy_status를 포함한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("deploy 상태 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "deploy_status" in {tool.name for tool in request.tools}
