"""실시간 조회 evidence 분리 회귀 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator


def _text_response(text: str, backend: str = "gemini") -> MagicMock:
    """도구 호출 없는 LLM 응답 mock을 만든다."""
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = None
    resp.backend_name = backend
    resp.raw_assistant_message = None
    return resp


@pytest.fixture
def config_file(tmp_path):
    """테스트용 최소 config.yaml을 만든다."""
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
  max_tool_iterations: 3

security:
  command_guard:
    enabled: true
    allowlist: []
  env_passthrough: []

skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
  execution_timeout: 30

persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: "AGENT.md"
      type: "agent"
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return config


def _make_orchestrator_with_realtime_skill(config_file, tmp_path):
    """realtime-lookup-skill이 등록된 orchestrator를 만든다."""
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
        orchestrator = AgentOrchestrator(config_file)

    mock_skill = MagicMock()
    mock_skill.name = "realtime-lookup-skill"
    mock_skill.description = "Return fresh evidence JSON for live factual questions"
    mock_skill.trigger = "뉴스, 날씨, 주가, 경기, 실시간"
    mock_skill.skill_dir = str(tmp_path)
    mock_skill.script_path = str(tmp_path / "realtime_lookup.py")

    orchestrator._reload_dynamic_files = MagicMock()
    orchestrator._skills = [mock_skill]
    orchestrator._skills_by_name = {"realtime-lookup-skill": mock_skill}
    orchestrator._skills_prompt = (
        "## Available Skills\n\n"
        "- **realtime-lookup-skill**: Return fresh evidence JSON for live factual questions"
    )
    return orchestrator


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_live_fact_final_answer_uses_realtime_lookup_skill_without_synthetic_web_fetch(
    config_file,
    tmp_path,
):
    """실시간 final 환각 게이트는 synthetic web_fetch assistant tool_call을 만들지 않는다."""
    orchestrator = _make_orchestrator_with_realtime_skill(config_file, tmp_path)
    evidence = (
        '{"kind":"news","query":"오늘 AI 뉴스 알려줘","freshness":"live",'
        '"facts":["AI evidence"],"evidence":[{"title":"AI News","url":"https://example.com"}],'
        '"limitations":[]}'
    )
    orchestrator._dispatch_tool_call = AsyncMock(return_value=evidence)
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[
            _text_response("근거 없이 바로 답합니다."),
            _text_response("근거 확인 결과: AI evidence (출처: AI News)"),
        ]
    )

    result = await orchestrator.process_message("오늘 AI 뉴스 알려줘", 1, 1)

    assert result == "근거 확인 결과: AI evidence (출처: AI News)"
    orchestrator._dispatch_tool_call.assert_awaited_once()
    realtime_call = orchestrator._dispatch_tool_call.await_args.args[0]
    assert realtime_call.name == "execute_skill"
    assert realtime_call.arguments["skill_name"] == "realtime-lookup-skill"
    assert realtime_call.arguments["args"] == "오늘 AI 뉴스 알려줘"

    second_request = orchestrator._router.send.call_args_list[1][0][0]
    assistant_tool_messages = [
        msg for msg in second_request.messages
        if msg.get("role") == "assistant" and msg.get("tool_calls")
    ]
    assert assistant_tool_messages == []
    assert all(
        tool_call.get("name") != "web_fetch"
        for msg in second_request.messages
        for tool_call in msg.get("tool_calls", [])
    )
    evidence_messages = [
        msg for msg in second_request.messages
        if msg.get("role") == "user" and "realtime-lookup evidence" in msg.get("content", "")
    ]
    assert evidence_messages
    assert "AI evidence" in evidence_messages[-1]["content"]


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_realtime_lookup_result_is_used_as_evidence_for_final_answer(
    config_file,
    tmp_path,
):
    """실시간 evidence 결과를 주입한 뒤 최종 답변 재생성을 강제한다."""
    orchestrator = _make_orchestrator_with_realtime_skill(config_file, tmp_path)
    evidence = (
        '{"kind":"weather","query":"오늘 서울 날씨","freshness":"live",'
        '"facts":["서울 18도, 흐림"],'
        '"evidence":[{"title":"기상청","url":"https://weather.example"}],'
        '"limitations":[]}'
    )
    orchestrator._dispatch_tool_call = AsyncMock(return_value=evidence)
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[
            _text_response("아마 맑을 거예요."),
            _text_response("실시간 근거에 따르면 서울은 18도이고 흐립니다."),
        ]
    )

    result = await orchestrator.process_message("오늘 서울 날씨", 1, 1)

    assert result == "실시간 근거에 따르면 서울은 18도이고 흐립니다."
    assert orchestrator._router.send.call_count == 2
    second_request = orchestrator._router.send.call_args_list[1][0][0]
    assert any("서울 18도, 흐림" in msg.get("content", "") for msg in second_request.messages)


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_non_live_question_does_not_invoke_realtime_lookup(config_file, tmp_path):
    """일반 설명형 질문은 실시간 lookup skill을 선조회하지 않는다."""
    orchestrator = _make_orchestrator_with_realtime_skill(config_file, tmp_path)
    orchestrator._dispatch_tool_call = AsyncMock(return_value="should not run")
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(return_value=_text_response("TDD는 테스트를 먼저 씁니다."))

    result = await orchestrator.process_message("TDD가 뭐야?", 1, 1)

    assert result == "TDD는 테스트를 먼저 씁니다."
    orchestrator._dispatch_tool_call.assert_not_awaited()
    assert orchestrator._router.send.call_count == 1


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
@pytest.mark.asyncio
async def test_ai_report_cron_does_not_fail_with_gemini_thought_signature_error(
    config_file,
    tmp_path,
):
    """cron 실시간 프롬프트도 raw thought_signature 없는 synthetic functionCall을 만들지 않는다."""
    orchestrator = _make_orchestrator_with_realtime_skill(config_file, tmp_path)
    orchestrator._dispatch_tool_call = AsyncMock(return_value='{"facts":["AI 뉴스 근거"]}')
    orchestrator._router = MagicMock()
    orchestrator._router.send = AsyncMock(
        side_effect=[
            _text_response("근거 없이 ai-report 작성"),
            _text_response("AI 뉴스 근거로 작성한 ai-report"),
        ]
    )

    result = await orchestrator.process_cron_message("오늘 최신 AI 뉴스 기반 ai-report 작성")

    assert result == "AI 뉴스 근거로 작성한 ai-report"
    second_request = orchestrator._router.send.call_args_list[1][0][0]
    assert all(
        not (msg.get("role") == "assistant" and msg.get("tool_calls"))
        for msg in second_request.messages
    )
