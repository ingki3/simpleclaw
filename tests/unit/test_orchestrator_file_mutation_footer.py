"""Orchestrator _tool_loop ↔ FileMutationTracker 통합 테스트 (BIZ-251).

DoD 일치 항목:
- 스킬이 의도한 파일을 안 쓴 케이스에서 ReAct 루프가 다음 턴에 재시도하는 통합 테스트 1건.
- footer 가 있는 / 없는 경우(변경 무) 모두 토큰량 회귀 측정.

여기서는 footer 부착 동작 자체를 검증한다 (LLM 응답을 mock 으로 고정하여
재시도 로직이 발화 가능한 입력 모양을 직접 확인).
"""

from __future__ import annotations

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import LLMResponse, ToolCall


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
llm:
  default: "gemini"
  providers:
    gemini:
      type: "api"
      model: "gemini-2.0-flash"
      api_key: "test-key"

agent:
  history_limit: 3
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 4
  workspace_dir: "{tmp_path}/workspace"

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
    - name: "MEMORY.md"
      type: "memory"

memory:
  rag:
    enabled: false
""")
    (tmp_path / "workspace").mkdir()
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\n")
    (persona_dir / "MEMORY.md").write_text("# Memory\n")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg, tmp_path


def _tool_response(call_id: str, name: str, args: dict | None = None) -> LLMResponse:
    return LLMResponse(
        text="",
        model="test",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args or {})],
    )


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="test", tool_calls=None)


# ----------------------------------------------------------------------
# DoD #1 — silent-fail 회복 통합 시나리오
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skill_no_write_marker_triggers_retry(config_file, monkeypatch):
    """스킬이 ``report.md`` 를 쓴다고 주장했지만 실제로는 디스크에 아무것도
    안 썼을 때, footer 의 ``[file changes this turn: none]`` 신호를 받은 LLM 이
    다음 iteration 에 file_write 로 직접 재시도하는 경로가 확보되어야 한다.

    검증 포인트:
    1. 1st iteration 직후의 tool result 메시지에 ``none`` 마커가 부착된다.
    2. LLM 이 footer 를 본 다음 iteration 에서 file_write 를 발화할 기회가 있다.
    3. file_write 가 실제로 디스크에 쓰면 그 iteration 의 footer 에 ``+ ...``
       로 보고된다 (재시도 성공의 검증 가능 신호).
    """
    cfg, root = config_file
    orch = AgentOrchestrator(cfg)

    # execute_skill: "성공" 을 주장하지만 디스크는 안 건드림 (silent fail).
    # file_write: 실제로 워크스페이스에 파일을 쓴다.
    workspace = root / "workspace"

    async def fake_dispatch(tc):
        if tc.name == "execute_skill":
            return "OK: report.md generated."
        if tc.name == "file_write":
            target = workspace / tc.arguments["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tc.arguments["content"])
            return f"wrote {tc.arguments['path']}"
        raise AssertionError(f"unexpected tool: {tc.name}")

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    # LLM mock — 가장 단순한 ReAct 스크립트:
    #   1. execute_skill 호출 (디스크 무변동 → footer 마커)
    #   2. file_write 호출 (실제 쓰기 → footer 가 + 로 보고)
    #   3. 최종 텍스트
    responses = [
        _tool_response(
            "c1", "execute_skill",
            {"skill_name": "report-gen", "args": "--out report.md"},
        ),
        _tool_response(
            "c2", "file_write",
            {"path": "report.md", "content": "# Report\n\nGenerated.\n"},
        ),
        _text_response("리포트 생성 완료 (재시도 후)."),
    ]
    seen_messages: list[list[dict]] = []
    call_idx = {"i": 0}

    async def fake_send(request):
        # request.messages 는 *현재 iteration 진입 시점* 의 컨텍스트.
        # snapshot 해서 footer 부착 결과를 검사할 수 있게 둔다.
        seen_messages.append([dict(m) for m in request.messages])
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("리포트 만들어줘")

    # 1) 2nd LLM 호출 직전 컨텍스트 (= 1st iteration tool result 가 들어간 상태) 에
    # ``[file changes this turn: none]`` 가 마지막 tool 메시지에 붙어야 한다.
    assert len(seen_messages) >= 2, "최소 2회 LLM 호출이 일어나야 함"
    second_call_messages = seen_messages[1]
    tool_msgs = [m for m in second_call_messages if m["role"] == "tool"]
    assert tool_msgs, "1st iteration 의 tool result 가 messages 에 있어야 함"
    last_tool = tool_msgs[-1]
    assert "[file changes this turn: none]" in last_tool["content"], (
        f"silent-fail 마커가 footer 로 부착되지 않음:\n{last_tool['content']!r}"
    )

    # 2) 3rd LLM 호출 (= 최종 텍스트 생성) 직전 컨텍스트에는 file_write 가
    # 실제로 쓴 사실이 + 로 보고되어야 한다.
    assert len(seen_messages) >= 3
    third_call_messages = seen_messages[2]
    tool_msgs = [m for m in third_call_messages if m["role"] == "tool"]
    last_tool = tool_msgs[-1]
    assert "+ .agent/workspace/report.md" in last_tool["content"], (
        f"실제 디스크 쓰기가 footer 로 보고되지 않음:\n{last_tool['content']!r}"
    )

    # 3) 정상 종료 (실제 디스크 결과로 LLM 이 최종 텍스트를 만듦).
    assert result == "리포트 생성 완료 (재시도 후)."
    assert (workspace / "report.md").exists()


# ----------------------------------------------------------------------
# DoD #2 — 토큰 회귀 (footer 있을 때 / 없을 때)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_footer_when_readonly_tool_and_no_disk_changes(
    config_file, monkeypatch,
):
    """read-only 도구 (web_fetch 등) 호출 + 디스크 변경 없음 → footer 생략.
    토큰 절약 DoD 의 회귀 가드."""
    cfg, _ = config_file
    orch = AgentOrchestrator(cfg)

    async def fake_dispatch(tc):
        return "<html>cached page</html>"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    responses = [
        _tool_response("c1", "web_fetch", {"url": "https://example.com"}),
        _text_response("페이지 내용 요약: ..."),
    ]
    seen_messages: list[list[dict]] = []
    call_idx = {"i": 0}

    async def fake_send(request):
        seen_messages.append([dict(m) for m in request.messages])
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    await orch.process_cron_message("페이지 요약")

    # 2nd 호출 직전 messages 의 마지막 tool 메시지에 footer 마커가 *없어야* 함.
    second_call = seen_messages[1]
    last_tool = next(m for m in reversed(second_call) if m["role"] == "tool")
    assert "[file changes this turn" not in last_tool["content"], (
        f"read-only 도구에 footer 가 부착됨 (토큰 낭비):\n"
        f"{last_tool['content']!r}"
    )


@pytest.mark.asyncio
async def test_footer_reports_added_file_after_file_write(
    config_file, monkeypatch,
):
    """file_write 가 실제로 파일을 쓰면 다음 iteration footer 에 ``+`` 로 보고."""
    cfg, root = config_file
    orch = AgentOrchestrator(cfg)
    workspace = root / "workspace"

    async def fake_dispatch(tc):
        if tc.name == "file_write":
            target = workspace / tc.arguments["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(tc.arguments["content"])
            return f"wrote {tc.arguments['path']}"
        return "ok"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    responses = [
        _tool_response(
            "c1", "file_write",
            {"path": "notes.md", "content": "line1\nline2\nline3\n"},
        ),
        _text_response("저장 완료"),
    ]
    seen_messages: list[list[dict]] = []
    call_idx = {"i": 0}

    async def fake_send(request):
        seen_messages.append([dict(m) for m in request.messages])
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    await orch.process_cron_message("notes 저장")

    second_call = seen_messages[1]
    last_tool = next(m for m in reversed(second_call) if m["role"] == "tool")
    assert "[file changes this turn]" in last_tool["content"]
    assert "+ .agent/workspace/notes.md (3 lines)" in last_tool["content"]


@pytest.mark.asyncio
async def test_footer_reports_persona_memory_modification(
    config_file, monkeypatch,
):
    """MEMORY.md 같은 페르소나 화이트리스트 파일 변경도 같은 footer 에
    묶여 보고되어야 한다 — 드리밍 외 경로로의 페르소나 수정 감시."""
    cfg, root = config_file
    orch = AgentOrchestrator(cfg)
    persona = root / "persona_local"

    async def fake_dispatch(tc):
        # 도구가 페르소나 dir 의 MEMORY.md 를 직접 갱신하는 시나리오 시뮬레이션
        # (실제 dreaming 패스 외에 봇이 직접 쓰는 경로).
        (persona / "MEMORY.md").write_text("# Memory\n- new insight\n")
        return "memory updated"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "memory-update"}),
        _text_response("기억 갱신 완료"),
    ]
    seen_messages: list[list[dict]] = []
    call_idx = {"i": 0}

    async def fake_send(request):
        seen_messages.append([dict(m) for m in request.messages])
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    await orch.process_cron_message("기억 갱신해줘")

    second_call = seen_messages[1]
    last_tool = next(m for m in reversed(second_call) if m["role"] == "tool")
    assert "M .agent/MEMORY.md" in last_tool["content"], (
        f"페르소나 MEMORY.md 변경이 footer 에 보고되지 않음:\n"
        f"{last_tool['content']!r}"
    )
