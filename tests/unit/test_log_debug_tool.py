"""log_debug 운영자 native tool 회귀 테스트."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.log_debug import handle_log_debug
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_schemas import ToolScope, build_tool_definitions
from simpleclaw.llm.models import ToolCall


def test_log_debug_is_operator_scoped_only():
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

    assert "log_debug" not in runtime_names
    assert "log_debug" in operator_names


def test_log_debug_recent_redacts_tokens_and_clips_user_content(tmp_path):
    """최근 로그 조회는 토큰과 긴 사용자 원문을 그대로 노출하지 않는다."""
    log_file = tmp_path / "bot.log"
    long_text = "사용자 입력 " + ("가나다" * 50)
    log_file.write_text(
        "2026-06-14 INFO boot ok\n"
        "2026-06-14 INFO telegram bot_token=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd token=plainsecret1234567890\n"
        f"2026-06-14 INFO message='{long_text}'\n",
        encoding="utf-8",
    )

    payload = json.loads(handle_log_debug({"action": "recent", "lines": 10}, log_path=log_file))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["matched"] == 3
    assert "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd" not in serialized
    assert "plainsecret1234567890" not in serialized
    assert "[REDACTED]" in serialized
    assert "[CLIPPED" in serialized


def test_log_debug_filters_errors_trace_and_action_patterns(tmp_path):
    """action/trace_id/pattern 조합으로 운영자가 필요한 로그만 좁혀 볼 수 있다."""
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "INFO scheduler tick trace_id=aaa\n"
        "WARNING tool_loop max_tool_iterations reached trace_id=bbb\n"
        "ERROR recipe failed trace_id=ccc Traceback: boom\n"
        "INFO telegram sendMessage ok trace_id=ddd\n"
        "ERROR admin_api /admin/v1/health failed trace_id=eee\n",
        encoding="utf-8",
    )

    errors = json.loads(handle_log_debug({"action": "errors", "lines": 2}, log_path=log_file))
    trace = json.loads(handle_log_debug({"action": "trace", "trace_id": "bbb"}, log_path=log_file))
    recipe = json.loads(handle_log_debug({"action": "recipe"}, log_path=log_file))
    telegram = json.loads(handle_log_debug({"action": "telegram", "pattern": "sendmessage"}, log_path=log_file))

    assert errors["matched"] == 2
    assert errors["lines"][-1].startswith("ERROR admin_api")
    assert trace["lines"] == ["WARNING tool_loop max_tool_iterations reached trace_id=bbb"]
    assert recipe["lines"] == ["ERROR recipe failed trace_id=ccc Traceback: boom"]
    assert telegram["lines"] == ["INFO telegram sendMessage ok trace_id=ddd"]


def test_log_debug_line_limits_and_missing_file_error(tmp_path):
    """lines는 1~200으로 제한하고 로그 파일 읽기 실패도 JSON 오류로 반환한다."""
    log_file = tmp_path / "bot.log"
    log_file.write_text("\n".join(f"line {idx}" for idx in range(250)), encoding="utf-8")

    payload = json.loads(handle_log_debug({"action": "recent", "lines": 999}, log_path=log_file))
    missing = json.loads(handle_log_debug({}, log_path=tmp_path / "missing.log"))

    assert payload["lines_requested"] == 200
    assert len(payload["lines"]) == 200
    assert payload["lines"][0] == "line 50"
    assert missing["ok"] is False
    assert "log file not found" in missing["error"]


@pytest.mark.asyncio
async def test_orchestrator_log_debug_dispatch_requires_operator_context(tmp_path, monkeypatch):
    """수동 dispatch도 operator context가 아니면 log_debug를 실행하지 않는다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    monkeypatch.setattr(
        "simpleclaw.agent.tool_dispatch.handle_log_debug",
        lambda args: json.dumps({"ok": True, "action": args["action"]}),
    )
    orchestrator = AgentOrchestrator(config)
    tool_call = ToolCall(id="log-1", name="log_debug", arguments={"action": "errors"})

    blocked = await orchestrator._dispatch_tool_call(tool_call)
    allowed = await orchestrator._dispatch_tool_call(tool_call, operator_tools=True)

    assert "operator context" in blocked
    assert json.loads(allowed) == {"ok": True, "action": "errors"}


@pytest.mark.asyncio
async def test_process_operator_message_exposes_log_debug_tool(tmp_path, monkeypatch):
    """operator message 경로는 LLMRequest tools에 log_debug를 포함한다."""
    config = tmp_path / "config.yaml"
    config.write_text(f"agent:\n  db_path: {tmp_path / 'conversation.db'}\n", encoding="utf-8")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    response = MagicMock()
    response.text = "ok"
    response.tool_calls = None
    orchestrator._router.send = AsyncMock(return_value=response)

    result = await orchestrator.process_operator_message("최근 에러 로그 확인")
    request = orchestrator._router.send.call_args.args[0]

    assert result == "ok"
    assert "log_debug" in {tool.name for tool in request.tools}
