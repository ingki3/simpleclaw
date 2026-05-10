"""Orchestrator의 tool loop fallback 동작 테스트 (BIZ-160).

검증 범위:
- max_tool_iterations 도달 + 최종 LLM 응답이 빈 문자열 → 사용자 안내 메시지 반환
- max_tool_iterations 도달 + 최종 LLM 응답이 의미 있음 → 한도 도달 안내 한 줄 부보
- 두 분기 모두에서 사용된 tool 시퀀스가 logger.warning 으로 박제됨
- tool loop 내부 일반 경로(텍스트 응답)는 영향을 받지 않음
"""

from __future__ import annotations

import logging

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
  max_tool_iterations: 2

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

memory:
  rag:
    enabled: false
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


def _tool_response(call_id: str, name: str, args: dict | None = None) -> LLMResponse:
    """tool_calls 가 있는 LLM 응답 mock."""
    return LLMResponse(
        text="",
        model="test",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args or {})],
    )


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="test", tool_calls=None)


@pytest.mark.asyncio
async def test_empty_final_response_returns_user_friendly_message(
    config_file, monkeypatch, caplog,
):
    """예산 소진 후 최종 LLM 응답이 비어 있으면 안내 메시지로 치환되어야 한다."""
    orch = AgentOrchestrator(config_file)

    # web_fetch / skill_docs 도구 핸들러 mock — 실제 네트워크/디스크 호출 차단
    async def fake_dispatch(tc):
        return f"[stub result for {tc.name}]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    # 2회 모두 tool_calls 를 돌려 받아 예산 소진 → 마지막 LLM 호출에서 빈 텍스트
    responses = [
        _tool_response("c1", "web_fetch"),
        _tool_response("c2", "skill_docs"),
        _text_response(""),  # 빈 final
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    with caplog.at_level(logging.WARNING, logger="simpleclaw.agent.orchestrator"):
        result = await orch.process_cron_message("뭐든 해줘")

    assert "여러 도구를 시도했지만" in result
    assert "tool loop 2회 반복 후 종료" in result

    # 호출 횟수: 2회 tool 응답 + 1회 forced final = 3
    assert call_idx["i"] == 3

    # logger.warning 이 tool 시퀀스를 박제했는지
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("tool_sequence=" in r.getMessage() for r in warnings), (
        "tool 시퀀스가 logger.warning 에 박제되어야 한다"
    )
    seq_msg = next(r.getMessage() for r in warnings if "tool_sequence=" in r.getMessage())
    assert "web_fetch" in seq_msg
    assert "skill_docs" in seq_msg


@pytest.mark.asyncio
async def test_non_empty_final_response_gets_hint_suffix(
    config_file, monkeypatch, caplog,
):
    """예산 소진 후 의미 있는 텍스트가 오면 한도 도달 안내가 한 줄 추가되어야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return f"[stub result for {tc.name}]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    final_text = "요약: 페이지를 가져오는 데 일부 정보가 부족합니다."
    responses = [
        _tool_response("c1", "web_fetch"),
        _tool_response("c2", "execute_skill"),
        _text_response(final_text),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    with caplog.at_level(logging.WARNING, logger="simpleclaw.agent.orchestrator"):
        result = await orch.process_cron_message("페이지 요약")

    assert result.startswith(final_text)
    assert "도구 호출 한도 2회에 도달" in result

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    seq_msg = next(
        (r.getMessage() for r in warnings if "tool_sequence=" in r.getMessage()),
        None,
    )
    assert seq_msg is not None
    assert "web_fetch" in seq_msg
    assert "execute_skill" in seq_msg


@pytest.mark.asyncio
async def test_normal_text_response_unaffected(config_file):
    """tool 호출 없이 텍스트만 돌아오는 일반 경로는 변경되지 않아야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_send(_request):
        return _text_response("정상 답변입니다.")

    orch._router.send = fake_send

    result = await orch.process_cron_message("안녕")
    assert result == "정상 답변입니다."
    assert "한도" not in result
    assert "tool loop" not in result
