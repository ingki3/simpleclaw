"""Orchestrator의 tool loop fallback 동작 테스트 (BIZ-160).

검증 범위:
- max_tool_iterations 도달 + 최종 LLM 응답이 빈 문자열 → 사용자 안내 메시지 반환
- max_tool_iterations 도달 + 최종 LLM 응답이 의미 있음 → 한도 도달 안내 한 줄 부보
- 두 분기 모두에서 사용된 tool 시퀀스가 logger.warning 으로 박제됨
- tool loop 내부 일반 경로(텍스트 응답)는 영향을 받지 않음
"""

from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_empty_direct_text_response_returns_fallback(config_file):
    """tool_calls 없이 빈 최종 텍스트가 와도 사용자에게 빈 메시지를 보내지 않는다."""
    orch = AgentOrchestrator(config_file)

    async def fake_send(_request):
        return _text_response("   ")

    orch._router.send = fake_send

    result = await orch.process_cron_message("안녕")
    assert "응답을 생성하지 못했습니다" in result
    assert result.strip()


@pytest.mark.asyncio
async def test_empty_final_after_empty_tool_result_reports_not_found(
    config_file, monkeypatch,
):
    """도구가 빈 결과를 반환한 뒤 LLM final 이 비면 '못 찾음'으로 답해야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return ""

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "cli", {"command": "sqlite3 conversations.db SELECT ..."}),
        _text_response("   "),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("예전에 김경열님과 골프 일정 넣었나?")

    assert "찾지 못했습니다" in result
    assert "빈 응답" not in result
    assert call_idx["i"] == 2


@pytest.mark.asyncio
async def test_empty_final_after_zero_rows_tool_result_reports_not_found(
    config_file, monkeypatch,
):
    """도구 결과가 0 rows 성격이면 빈 final 대신 '못 찾음'으로 답해야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "김경열 골프 일정 검색 결과: 0 rows"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "conversation_search", {"query": "김경열 골프"}),
        _text_response("   "),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message(
        "김경열님과 골프 일정을 넣어달라고 한 적이 있었나?",
    )

    assert result.strip()
    assert "찾지 못했습니다" in result
    assert "응답을 생성하지 못했습니다" not in result
    assert call_idx["i"] == 2


@pytest.mark.asyncio
async def test_empty_final_after_tool_error_reports_checked_but_failed(
    config_file, monkeypatch,
):
    """도구 오류 뒤 빈 final 이 오면 재질문 대신 확인 실패 사실을 알려야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "Error: sqlite3 database is locked"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "cli", {"command": "sqlite3 conversations.db SELECT ..."}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("예전 골프 일정 확인해줘")

    assert "확인 중 오류" in result
    assert "sqlite3 database is locked" in result
    assert "한 번 더 말씀" not in result


@pytest.mark.asyncio
async def test_empty_final_after_transcript_with_error_words_reports_generic_result(
    config_file, monkeypatch,
):
    """정상 transcript 본문 속 error/failed 단어는 도구 오류로 오판하지 않아야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return (
            "Transcript:\n"
            "This video explains how an agent can fail when context is noisy.\n"
            "The speaker also says previous approaches had an error rate problem.\n"
            "하지만 이 텍스트는 정상적으로 추출된 유튜브 transcript 본문입니다."
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response(
            "c1",
            "execute_skill",
            {"skill_name": "summarize", "args": "https://youtu.be/example --youtube auto"},
        ),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("https://youtu.be/example")

    assert "확인은 했지만 답변을 마무리하지 못했습니다" in result
    assert "execute_skill: Transcript:" in result
    assert "확인 중 오류" not in result
    assert "한 번 더 말씀" not in result


@pytest.mark.asyncio
async def test_forced_final_answer_timeout_returns_fallback(
    config_file, monkeypatch, caplog,
):
    """BIZ-141 — forced final-answer 호출이 hang 하면 timeout 으로 끊고
    사용자 친화 fallback 메시지를 반환해야 한다 (sendMessage 침묵 사고 방지).
    """
    import simpleclaw.agent.orchestrator as orch_mod

    # 테스트가 빨리 끝나도록 타임아웃을 0.1s 로 축소
    monkeypatch.setattr(orch_mod, "_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS", 0.1)

    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return f"[stub result for {tc.name}]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    # 도구 응답 2번 (max_tool_iterations=2) 으로 예산 소진 → 강제 final-answer.
    # 마지막 호출에서만 hang 하도록 시퀀스 구성.
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        if i < 2:
            # 도구 호출 응답
            return _tool_response(f"c{i}", "web_fetch")
        # 강제 final-answer 호출에서 hang
        await asyncio.sleep(5)
        return _text_response("not reached")

    orch._router.send = fake_send

    with caplog.at_level(logging.ERROR, logger="simpleclaw.agent.orchestrator"):
        result = await orch.process_cron_message("뭐든 해줘")

    assert "응답이 지연되어 처리를 종료했습니다" in result, (
        "타임아웃 시 사용자에게 fallback 메시지가 전달되어야 한다"
    )
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "final generation timeout" in r.getMessage() for r in errors
    ), "ERROR 로그에 timeout 사실이 박제되어야 한다"


# ----------------------------------------------------------------------
# BIZ-190 — per-turn agent-browser 호출 cap
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_browser_per_turn_cap_synthesizes_blocked_response(
    config_file, monkeypatch, caplog,
):
    """BIZ-190 — 같은 turn 안에서 ``agent-browser`` 호출이 cap 을 넘으면
    subprocess 로 흐르지 않고 합성 차단 응답이 tool result 로 들어가야 한다.

    seed-2/3/8/9 (2026-05-13 20:19~20:36 KST) 의 4건 max-iter 사고는 첫
    agent-browser 호출 실패(daemon busy 등) 후 LLM 이 같은 명령을
    execute_skill/cli 채널로 재시도하면서 누적 소진하는 패턴.
    """
    import simpleclaw.agent.orchestrator as orch_mod

    # cap 을 1 로 낮춰 짧은 시퀀스로도 트리거 가능하게.
    monkeypatch.setattr(orch_mod, "_AGENT_BROWSER_PER_TURN_CALL_CAP", 1)

    orch = AgentOrchestrator(config_file)
    # max_tool_iterations 가 2 이므로 첫 turn 에 cap 트리거 + 두 번째 turn 에서 final
    # 텍스트가 들어가도록 시퀀스를 길게 잡는다. 카운트는 turn 단위가 아니라
    # tool loop 진입 1회 기준이므로 2회 모두 agent-browser 호출.

    dispatch_calls: list[str] = []

    async def fake_dispatch(tc):
        dispatch_calls.append(tc.name)
        return f"[stub result for {tc.name}]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    responses = [
        # 1번째 호출: agent-browser composite via execute_skill — cap 안쪽이라 dispatch 됨.
        _tool_response(
            "c1", "execute_skill",
            {"skill_name": "agent-browser", "args": "open https://wikidocs.net/3753"},
        ),
        # 2번째 호출: 같은 turn 안에서 또 agent-browser — cap 초과, dispatch 되지 않아야 함.
        _tool_response(
            "c2", "execute_skill",
            {"skill_name": "agent-browser", "args": "open https://wikidocs.net/"},
        ),
        # 강제 final-answer 호출에서 텍스트 반환.
        _text_response("죄송합니다, 사이트가 자동 회수를 차단합니다."),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    with caplog.at_level(
        logging.WARNING, logger="simpleclaw.agent.orchestrator"
    ):
        result = await orch.process_cron_message("wikidocs 페이지 회수")

    # cap 초과로 두 번째 agent-browser 호출은 dispatch 되지 않아야 함.
    assert dispatch_calls == ["execute_skill"], (
        f"두 번째 agent-browser 호출은 cap 으로 차단되어야 함, dispatch={dispatch_calls}"
    )
    # WARNING 로그에 cap 메시지가 박제되었는지.
    assert "agent-browser per-turn cap exceeded" in caplog.text
    # 사용자 응답이 정상적으로 전달되었는지 (cap 자체는 max-iter 와 무관).
    assert "사이트가 자동 회수를 차단합니다" in result


@pytest.mark.asyncio
async def test_agent_browser_under_cap_dispatches_normally(
    config_file, monkeypatch,
):
    """BIZ-190 회귀 가드 — cap 이내(첫 1회) 호출은 정상적으로 dispatch 된다."""
    orch = AgentOrchestrator(config_file)

    dispatch_calls: list[str] = []

    async def fake_dispatch(tc):
        dispatch_calls.append(tc.name)
        return f"[stub result for {tc.name}]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    responses = [
        _tool_response(
            "c1", "execute_skill",
            {"skill_name": "agent-browser", "args": "open https://x"},
        ),
        _text_response("정상 응답"),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("페이지 열어줘")
    assert result == "정상 응답"
    # cap=2 (기본) 이므로 1회는 dispatch 되어야 함.
    assert dispatch_calls == ["execute_skill"]


# ---------------------------------------------------------------------------
# BIZ-259 — streaming wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_threads_on_text_delta_to_router(
    config_file, monkeypatch,
):
    """``process_message(on_text_delta=...)`` 가 라우터까지 콜백을 전달해야 한다."""
    orch = AgentOrchestrator(config_file)

    seen_callback = {"cb": None}

    async def fake_send(request, on_text_delta=None):
        # 라우터 send 가 콜백을 받아 첫 델타를 흘려보낸 뒤 final 텍스트로 종료.
        seen_callback["cb"] = on_text_delta
        if on_text_delta is not None:
            await on_text_delta("hello ")
            await on_text_delta("world")
        return _text_response("hello world")

    orch._router.send = fake_send

    collected: list[str] = []

    async def cb(d: str) -> None:
        collected.append(d)

    result = await orch.process_message(
        "ping", user_id=1, chat_id=1, on_text_delta=cb,
    )
    assert result == "hello world"
    assert collected == ["hello ", "world"]
    assert seen_callback["cb"] is cb


@pytest.mark.asyncio
async def test_process_message_without_callback_uses_send_signature(
    config_file, monkeypatch,
):
    """BIZ-259 — 콜백 미지정 시 기존 1-인자 ``router.send(request)`` 시그니처 유지.

    fake_send 가 ``request`` 단일 인자만 받아도 호출이 성공해야 한다 (회귀 가드).
    """
    orch = AgentOrchestrator(config_file)

    async def fake_send(_request):  # 단일 인자
        return _text_response("plain answer")

    orch._router.send = fake_send

    result = await orch.process_message("hi", user_id=1, chat_id=1)
    assert result == "plain answer"
