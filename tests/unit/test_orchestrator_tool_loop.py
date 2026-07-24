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
from simpleclaw.agent.tool_loop import ToolLoopResult, ToolLoopRunner, ToolLoopState
from simpleclaw.llm.models import LLMResponse, MultimodalAttachment, ToolCall


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
  # BIZ-426 — tool loop 프롬프트/저장 동작은 결정적 fallback 경로로 검증.
  turn_analysis:
    enabled: false

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




def test_tool_loop_runner_contract_is_importable():
    """BIZ-346 — tool loop lifecycle은 전용 runner/dataclass 계약으로 분리된다."""

    assert ToolLoopRunner.__name__ == "ToolLoopRunner"
    assert set(ToolLoopState.__dataclass_fields__) >= {
        "user_content",
        "messages",
        "system_prompt",
        "tools",
        "system_blocks",
    }
    assert set(ToolLoopResult.__dataclass_fields__) >= {"text"}

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
async def test_tool_result_with_10000_chars_is_fully_present_in_next_llm_request(
    config_file, monkeypatch,
):
    """BIZ-479 — 3,000자를 넘는 tool result도 새 한도 안에서는 보존한다."""
    orch = AgentOrchestrator(config_file)
    tool_result = "x" * 10_000

    async def fake_dispatch(_tc):
        return tool_result

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        if len(seen_requests) == 1:
            return _tool_response("c1", "web_fetch")
        return _text_response("완료")

    orch._router.send = fake_send

    result = await orch.process_cron_message("긴 도구 결과 테스트")

    assert result == "완료"
    tool_message = next(
        message
        for message in seen_requests[1].messages
        if message.get("tool_call_id") == "c1"
    )
    assert tool_message["content"] == tool_result


@pytest.mark.asyncio
async def test_tool_result_with_20001_chars_is_capped_at_20000_in_next_llm_request(
    config_file, monkeypatch,
):
    """BIZ-479 — LLM으로 전달하는 tool result는 정확히 20,000자로 제한한다."""
    orch = AgentOrchestrator(config_file)
    tool_result = "y" * 20_000 + "z"

    async def fake_dispatch(_tc):
        return tool_result

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        if len(seen_requests) == 1:
            return _tool_response("c1", "web_fetch")
        return _text_response("완료")

    orch._router.send = fake_send

    result = await orch.process_cron_message("도구 결과 상한 테스트")

    assert result == "완료"
    tool_message = next(
        message
        for message in seen_requests[1].messages
        if message.get("tool_call_id") == "c1"
    )
    assert len(tool_message["content"]) == 20_000
    assert tool_message["content"] == tool_result[:20_000]


@pytest.mark.asyncio
async def test_attachment_context_note_is_in_current_user_message_not_saved(
    config_file,
):
    """문서 첨부 메타 note는 provider 요청에만 붙고 대화 DB 저장 텍스트에는 남지 않는다."""
    orch = AgentOrchestrator(config_file)
    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        return _text_response("첨부를 확인했습니다.")

    orch._router.send = fake_send
    attachment = MultimodalAttachment(
        data=b"%PDF-1.7",
        mime_type="application/pdf",
        name="paper.pdf",
        path="/tmp/simpleclaw-attachments/paper.pdf",
        size_bytes=8,
    )

    result = await orch.process_message(
        "요약해줘",
        user_id=1,
        chat_id=1,
        attachments=[attachment],
    )

    assert result == "첨부를 확인했습니다."
    assert len(seen_requests) == 1
    current_message = seen_requests[0].messages[-1]
    assert current_message["attachments"] == [attachment]
    content = current_message["content"]
    assert "Attachment context" in content
    assert "paper.pdf" in content
    assert "application/pdf" in content
    assert "/tmp/simpleclaw-attachments/paper.pdf" in content
    assert "8 bytes" in content
    assert "직접 분석" in content
    assert "불가능하면" in content
    assert "1차 근거" in content
    assert "이거" in content
    assert "몇 알" in content
    assert "명시적으로 요청하지 않았다면" in content
    assert "첨부와 무관한 웹 검색이나 현재 사실 조회" in content

    saved = orch._store.get_recent(limit=2)
    assert saved[0].content == "요약해줘"
    assert "Attachment context" not in saved[0].content
    assert "%PDF" not in saved[0].content
    assert "/tmp/simpleclaw-attachments/paper.pdf" not in saved[0].content


@pytest.mark.asyncio
async def test_attachment_context_note_includes_attachment_without_path(config_file):
    orch = AgentOrchestrator(config_file)
    seen_requests = []

    async def fake_send(request):
        seen_requests.append(request)
        return _text_response("이미지 확인")

    orch._router.send = fake_send
    attachment = MultimodalAttachment(
        data=b"jpg", mime_type="image/jpeg", name="photo.jpg"
    )

    await orch.process_message("이미지를 분석해 주세요.", 1, 1, attachments=[attachment])

    content = seen_requests[0].messages[-1]["content"]
    assert "photo.jpg" in content
    assert "image/jpeg" in content
    assert "Sandbox path" not in content


@pytest.mark.asyncio
async def test_live_fact_final_without_evidence_is_blocked_by_tool_loop(config_file):
    """BIZ-363: 최신 근거 없는 실시간 사실 final text는 tool loop가 fallback으로 차단한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_send(_request):
        return _text_response("대한민국 vs 우루과이: 6월 19일 10시 중계 예정입니다.")

    orch._router.send = fake_send
    state = ToolLoopState(
        user_content="이번 월드컵 한국 경기 중계 일정 알려줘",
        messages=[{"role": "user", "content": "이번 월드컵 한국 경기 중계 일정 알려줘"}],
        system_prompt="",
        tools=[],
        system_blocks=[],
        live_fact_requires_evidence=True,
        live_evidence_seen=False,
    )

    result = await ToolLoopRunner(orch).run(state)

    assert "확인하지 못" in result.text
    assert "6월 19일 10시" not in result.text


@pytest.mark.asyncio
async def test_live_fact_fetch_blocked_final_is_blocked(
    config_file, monkeypatch,
):
    """BIZ-363: FETCH_BLOCKED는 usable evidence가 아니므로 이후 final text도 fallback으로 차단한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return (
            "FETCH_BLOCKED: https://www.google.com/search?q=2026+World+Cup\n"
            "This site appears to block automated fetching."
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response(
            "c1",
            "web_fetch",
            {"url": "https://www.google.com/search?q=2026+World+Cup"},
        ),
        _text_response("대한민국 vs 미국: 6월 23일 22시에 중계됩니다."),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send
    state = ToolLoopState(
        user_content="이번 월드컵 한국 경기 중계 일정 알려줘",
        messages=[{"role": "user", "content": "이번 월드컵 한국 경기 중계 일정 알려줘"}],
        system_prompt="",
        tools=[],
        system_blocks=[],
        live_fact_requires_evidence=True,
        live_evidence_seen=False,
    )

    result = await ToolLoopRunner(orch).run(state)

    assert "확인하지 못" in result.text
    assert "6월 23일 22시" not in result.text


@pytest.mark.asyncio
async def test_low_confidence_structured_result_does_not_flip_live_evidence(
    config_file, monkeypatch,
):
    """confidence=low structured JSON은 tool loop의 live evidence gate를 열지 않는다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(_tc):
        return (
            '{"kind":"sports","confidence":"low",'
            '"facts":[{"type":"sports_score","away_score":2,"home_score":1}]}'
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response(
            "c1",
            "execute_skill",
            {"skill_name": "realtime-lookup-skill", "command": "payload"},
        ),
        _text_response("롯데가 2:1로 이겼고 경기는 LIVE입니다."),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        index = call_idx["i"]
        call_idx["i"] += 1
        return responses[index]

    orch._router.send = fake_send
    state = ToolLoopState(
        user_content="롯데 야구 어케 되었나?",
        messages=[{"role": "user", "content": "롯데 야구 어케 되었나?"}],
        system_prompt="",
        tools=[],
        system_blocks=[],
        live_fact_requires_evidence=True,
        live_evidence_seen=False,
    )

    result = await ToolLoopRunner(orch).run(state)

    assert "확인하지 못" in result.text
    assert "2:1" not in result.text
    assert "LIVE" not in result.text


@pytest.mark.asyncio
async def test_live_sports_query_does_not_synthesize_web_fetch_before_final_answer(
    config_file, monkeypatch,
):
    """실시간 경기 질문에서도 Gemini-breaking synthetic web_fetch를 만들지 않는다."""
    orch = AgentOrchestrator(config_file)

    dispatch_calls: list[ToolCall] = []

    async def fake_dispatch(tc):
        dispatch_calls.append(tc)
        return "네이버 스포츠 확인 결과: KT 7:3 SSG"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    call_idx = {"i": 0}

    async def fake_send(_request):
        call_idx["i"] += 1
        return _text_response("LG가 두산을 7:4로 이겼습니다.")

    orch._router.send = fake_send

    result = await orch.process_cron_message("오늘 프로야구 결과 알려줘")

    assert "확인하지 못" in result
    assert "7:4" not in result
    assert call_idx["i"] == 1
    assert dispatch_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "삼성전자 현재 주가 알려줘",
        "서울 날씨 지금 어때?",
        "AI 최신 뉴스 찾아줘",
    ],
)
async def test_live_market_weather_news_queries_do_not_synthesize_web_fetch(
    config_file, monkeypatch, message,
):
    """주가·날씨·뉴스 질문도 synthetic web_fetch 없이 모델/스킬 경로에 맡긴다."""
    orch = AgentOrchestrator(config_file)

    dispatch_calls: list[ToolCall] = []

    async def fake_dispatch(tc):
        dispatch_calls.append(tc)
        return f"웹 확인 결과: {message}"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    call_idx = {"i": 0}

    async def fake_send(_request):
        call_idx["i"] += 1
        return _text_response("조회 없이 만든 답변")

    orch._router.send = fake_send

    result = await orch.process_cron_message(message)

    assert "확인하지 못" in result
    assert "조회 없이 만든 답변" not in result
    assert call_idx["i"] == 1
    assert dispatch_calls == []


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
async def test_empty_final_prefers_prior_success_over_trailing_web_search_error(
    config_file, monkeypatch,
):
    """유효한 검색 결과 뒤 transient 검색 오류가 와도 fallback은 확인 결과를 보존한다."""
    orch = AgentOrchestrator(config_file)
    orch._max_tool_iterations = 3

    dispatch_results = [
        (
            "WEB_SEARCH_RESULTS: '노정의 마녀 드라마' (1 results)\n"
            "1. 마녀 - 드라마 정보\n"
            "   URL: https://example.com/witch\n"
            "   Snippet: 강풀 원작 드라마 마녀 출연진 정보."
        ),
        (
            "Error: web_search failed — DuckDuckGo returned HTTP 202 — Accepted. "
            "Try a more specific query, or use web_fetch if you already have a URL."
        ),
    ]

    async def fake_dispatch(tc):
        return dispatch_results.pop(0)

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "web_search", {"query": "노정의 마녀 드라마"}),
        _tool_response("c2", "web_search", {"query": "신은수 강풀 드라마"}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message(
        "노정의 신은수 배우가 나온 강풀 원작 드라마 찾아줘"
    )

    # BIZ-414: 유효한 web_search 뒤 transient 오류가 와도 확인된 title/URL 근거를 보존한다.
    assert "검색은 마쳤지만" in result
    assert "마녀 - 드라마 정보" in result
    assert "https://example.com/witch" in result
    # 240자 truncation 으로 raw 페이로드를 뭉개던 generic 경로는 더 이상 타지 않는다.
    assert "web_search: WEB_SEARCH_RESULTS" not in result
    assert "확인 중 오류" not in result
    assert "DuckDuckGo returned HTTP 202" not in result


@pytest.mark.asyncio
async def test_empty_final_after_web_search_preserves_title_and_url(
    config_file, monkeypatch,
):
    """web_search 성공 후 빈 final이면 결과 제목/URL을 fallback 근거로 보존한다 (BIZ-414)."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return (
            "WEB_SEARCH_RESULTS: '요즘 재미있는 뮤지컬' (3 results)\n"
            "1. 뮤지컬 '오페라의 유령' 서울 공연\n"
            "   URL: https://example.com/phantom\n"
            "   Snippet: 2026 상반기 화제작.\n"
            "2. 뮤지컬 '레미제라블' 앙코르\n"
            "   URL: https://example.com/lesmis\n"
            "   Snippet: 오리지널 내한.\n"
            "3. 뮤지컬 '데스노트'\n"
            "   URL: https://example.com/deathnote\n"
            "   Snippet: 재연 확정."
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "web_search", {"query": "요즘 재미있는 뮤지컬"}),
        _text_response("   "),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("요즘 재미있는 뮤지컬 있나 찾아봐")

    assert result.strip()
    # 최소 하나 이상의 title/URL 근거가 사용자에게 보존되어야 한다.
    assert "뮤지컬 '오페라의 유령' 서울 공연" in result
    assert "https://example.com/phantom" in result
    assert "뮤지컬 '레미제라블' 앙코르" in result
    assert "https://example.com/lesmis" in result
    # 일반 빈-응답/못 찾음 fallback으로 새지 않아야 한다.
    assert "응답을 생성하지 못했습니다" not in result
    assert "찾지 못했습니다" not in result
    assert call_idx["i"] == 2


@pytest.mark.asyncio
async def test_empty_final_preserves_evidence_from_earlier_web_search_not_just_last(
    config_file, monkeypatch,
):
    """마지막 결과만이 아니라 이전 유용한 web_search 결과도 fallback에 보존한다 (BIZ-414)."""
    orch = AgentOrchestrator(config_file)
    orch._max_tool_iterations = 3

    dispatch_results = [
        (
            "WEB_SEARCH_RESULTS: '뮤지컬 신작' (1 results)\n"
            "1. 신작 뮤지컬 A 개막\n"
            "   URL: https://example.com/new-a\n"
            "   Snippet: 3월 개막."
        ),
        (
            "WEB_SEARCH_RESULTS: '뮤지컬 앙코르' (1 results)\n"
            "1. 앙코르 뮤지컬 B\n"
            "   URL: https://example.com/encore-b\n"
            "   Snippet: 재연."
        ),
    ]

    async def fake_dispatch(tc):
        return dispatch_results.pop(0)

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "web_search", {"query": "뮤지컬 신작"}),
        _tool_response("c2", "web_search", {"query": "뮤지컬 앙코르"}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("요즘 볼만한 뮤지컬 신작이랑 앙코르 공연 찾아줘")

    # 두 web_search 모두의 근거가 fallback에 남아야 한다 (마지막 것만 아님).
    assert "신작 뮤지컬 A 개막" in result
    assert "https://example.com/new-a" in result
    assert "앙코르 뮤지컬 B" in result
    assert "https://example.com/encore-b" in result


@pytest.mark.asyncio
async def test_empty_final_after_only_no_output_tool_result_asks_for_more_direction(
    config_file, monkeypatch,
):
    """무의미한 성공 결과만 있으면 확인 결과 요약 대신 추가 단서/방향을 요청한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "[Command completed with no output]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "cli", {"command": "curl ... | grep ..."}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message(
        "노정의 신은수 배우가 나온 강풀 원작 드라마 찾아줘"
    )

    assert "확인한 범위만으로는 답을 확정하기 어렵습니다" in result
    assert "추가로 어떤 방향으로 확인할까요" in result
    assert "다른 키워드" in result
    assert "다른 출처" in result
    assert "조건을 추가" in result
    assert "URL 기준" in result
    assert "배우 기준" not in result
    assert "줄거리/설정 기준" not in result
    assert "방영 시기" not in result
    assert "[Command completed with no output]" not in result
    assert "확인은 했지만 답변을 마무리하지 못했습니다" not in result


@pytest.mark.asyncio
async def test_empty_final_skips_meta_tool_docs_and_keeps_kbo_evidence(
    config_file, monkeypatch,
):
    """도구 문서/검색 오류가 뒤따라도 사용자 질문의 실제 근거를 보존한다."""
    orch = AgentOrchestrator(config_file)
    orch._max_tool_iterations = 4

    dispatch_results = [
        (
            "(via headless render; force_headless=True)\n\n"
            "KBO 스코어보드 2026.07.02(목) "
            "롯데 0 4회말 0 두산 0-0 2out 잠실 18:30"
        ),
        (
            "[Skill documentation for agent-browser]: Browser automation for "
            "interactive website tasks. Use this skill when navigating pages."
        ),
        (
            "Error: web_search failed — DuckDuckGo returned HTTP 202 — Accepted. "
            "Try a more specific query, or use web_fetch if you already have a URL."
        ),
    ]

    async def fake_dispatch(tc):
        return dispatch_results.pop(0)

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response(
            "c1",
            "web_fetch",
            {"url": "https://www.koreabaseball.com/Schedule/ScoreBoard.aspx"},
        ),
        _tool_response("c2", "skill_docs", {"name": "agent-browser"}),
        _tool_response("c3", "web_search", {"query": "롯데 두산 우천 중단"}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("비 온다고 했던 거 같은데?")

    assert "확인한 근거는 있지만" in result
    assert "web_fetch:" in result
    assert "KBO 스코어보드" in result
    assert "롯데 0 4회말 0 두산" in result
    assert "agent-browser" not in result
    assert "Skill documentation" not in result
    assert "DuckDuckGo returned HTTP 202" not in result
    assert "배우" not in result
    assert "방영" not in result


@pytest.mark.asyncio
async def test_empty_final_prefers_prior_success_over_trailing_no_output_cli(
    config_file, monkeypatch,
):
    """유효 검색 결과 뒤 no-output CLI가 와도 검색 결과를 보존한다."""
    orch = AgentOrchestrator(config_file)
    orch._max_tool_iterations = 3

    dispatch_results = [
        (
            "WEB_SEARCH_RESULTS: '강풀 마녀 드라마 노정의' (1 results)\n"
            "1. 마녀 - 채널A 드라마\n"
            "   URL: https://example.com/witch\n"
            "   Snippet: 강풀 웹툰 원작, 노정의 주연."
        ),
        "[Command completed with no output]",
    ]

    async def fake_dispatch(tc):
        return dispatch_results.pop(0)

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "web_search", {"query": "강풀 마녀 드라마 노정의"}),
        _tool_response("c2", "cli", {"command": "curl ... | grep ..."}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("마녀 별명 드라마 제목 찾아줘")

    # BIZ-414: no-output CLI 가 뒤에 와도 앞선 web_search title/URL 근거를 보존한다.
    assert "검색은 마쳤지만" in result
    assert "마녀 - 채널A 드라마" in result
    assert "https://example.com/witch" in result
    assert "web_search: WEB_SEARCH_RESULTS" not in result
    assert "Command completed with no output" not in result
    assert "추가로 어떤 기준" not in result


@pytest.mark.asyncio
async def test_empty_final_no_evidence_creates_pending_clarify_in_chat(
    config_file, monkeypatch,
):
    """대화형 채널에서는 근거 부족 fallback이 인라인 clarify 질문으로 전환된다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "[Command completed with no output]"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "cli", {"command": "curl ... | grep ..."}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_message(
        "제목 찾아봐",
        user_id=6233568410,
        chat_id=6233568410,
    )

    assert "확인한 범위만으로는 답을 확정하기 어렵습니다" in result
    assert "다른 키워드로 다시 확인해줘" in result
    pending = orch.pop_pending_clarify(6233568410)
    assert pending is not None
    assert "어떤 방향으로 확인할까요" in pending.question
    assert [opt.label for opt in pending.options] == [
        "다른 키워드",
        "다른 출처",
        "조건 추가",
        "URL로 확인",
    ]


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

    assert "확인한 근거는 있지만" in result
    assert "execute_skill: Transcript:" in result
    assert "확인 중 오류" not in result
    assert "한 번 더 말씀" not in result


@pytest.mark.asyncio
async def test_empty_final_after_command_failed_header_reports_error(
    config_file, monkeypatch,
):
    """명시적인 오류 헤더는 계속 확인 실패 fallback으로 분류해야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "Command failed: summarize exited with status 1\nstderr: network timeout"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "summarize"}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("이 유튜브 요약해줘")

    assert "확인 중 오류" in result
    assert "Command failed" in result


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


# ── BIZ-436: ActionResultLedger 기반 empty-final 복구 ─────────────────


@pytest.mark.asyncio
async def test_empty_final_after_calendar_create_reports_success_from_ledger(
    config_file, monkeypatch,
):
    """calendar create 성공 뒤 Gemini final 이 비어도 '확정 못함'이 아니라 완료를 보고해야 한다."""
    orch = AgentOrchestrator(config_file)
    orch._max_tool_iterations = 3

    async def fake_dispatch(tc):
        if tc.name == "skill_docs":
            return "[Skill documentation for google-calendar-skill]"
        return (
            "Creating event...\n"
            "Event created successfully: https://www.google.com/calendar/event?eid=abc\n"
            "Event ID: 1l8ivhtgrt68f9h9i4n6s7f1d0"
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "skill_docs", {"name": "google-calendar-skill"}),
        _tool_response(
            "c2",
            "execute_skill",
            {
                "skill_name": "google-calendar-skill",
                "args": "create --calendar-name 골프 --summary '해비치 박민재 골프'",
            },
        ),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("7월 26일 해비치 박민재 골프 일정 추가해줘")

    assert "작업이 완료됐습니다" in result
    assert "1l8ivhtgrt68f9h9i4n6s7f1d0" in result
    assert "확정" not in result
    assert "답변을 마무리하지 못했습니다" not in result


@pytest.mark.asyncio
async def test_empty_final_after_partial_success_reports_completed_and_failed_steps(
    config_file, monkeypatch,
):
    """여러 tool 중 일부 side-effect 성공 후 실패가 있어도 완료된 작업을 숨기지 않는다."""
    orch = AgentOrchestrator(config_file)
    orch._max_tool_iterations = 3

    dispatch_outputs = [
        (
            "Creating event...\n"
            "Event created successfully: https://www.google.com/calendar/event?eid=abc\n"
            "Event ID: evt123"
        ),
        "Error executing skill reminder-skill: scheduler unavailable",
    ]

    async def fake_dispatch(tc):
        return dispatch_outputs.pop(0)

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "google-calendar-skill", "args": "create ..."}),
        _tool_response("c2", "execute_skill", {"skill_name": "reminder-skill", "args": "create ..."}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("일정 추가하고 리마인더도 걸어줘")

    assert "일부" in result
    assert "evt123" in result
    assert "scheduler unavailable" in result
    assert "전체 실패" not in result


@pytest.mark.asyncio
async def test_forced_final_answer_request_does_not_include_tools(
    config_file, monkeypatch,
):
    """forced final-answer 단계는 side-effect tool 재실행을 막기 위해 tools 없이 호출되어야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "tool output"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)

    seen_requests = []
    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "google-calendar-skill"}),
        _tool_response("c2", "execute_skill", {"skill_name": "google-calendar-skill"}),
        _text_response("최종 답변"),
    ]
    call_idx = {"i": 0}

    async def fake_send(request):
        seen_requests.append(request)
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("반복 도구 테스트")

    assert result.startswith("최종 답변")
    assert seen_requests[-1].tools is None


@pytest.mark.asyncio
async def test_empty_final_log_includes_usage_metadata(config_file, monkeypatch, caplog):
    """empty-final 경고 로그에 최소한 usage 메타데이터가 남아 원인 분석이 가능해야 한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return "Creating event...\nEvent created successfully: url\nEvent ID: evt123"

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "google-calendar-skill"}),
        LLMResponse(
            text="",
            model="test",
            tool_calls=None,
            usage={"input_tokens": 100, "output_tokens": 0},
        ),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    with caplog.at_level(logging.WARNING, logger="simpleclaw.agent.orchestrator"):
        await orch.process_cron_message("일정 추가")

    assert "empty final answer" in caplog.text
    assert "output_tokens" in caplog.text


# ── BIZ-437: first-line error/failed 단어 오분류 방지 ─────────────────


@pytest.mark.asyncio
async def test_empty_final_after_first_line_failed_transcript_reports_generic_result(
    config_file, monkeypatch,
):
    """'Failed ...' 문장으로 시작하는 정상 결과 뒤 empty final 은 오류가 아니라 generic 근거로 답한다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return (
            "Failed attempts are normal in agent workflows and the speaker "
            "explains how retries recover from them."
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "summarize"}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("이 영상 요약해줘")

    assert "확인한 근거는 있지만" in result
    assert "Failed attempts are normal" in result
    assert "확인 중 오류" not in result


@pytest.mark.asyncio
async def test_empty_final_after_first_line_error_rates_transcript_reports_generic_result(
    config_file, monkeypatch,
):
    """'Error rates ...' 문장으로 시작하는 정상 결과 뒤 empty final 도 오류로 가지 않는다."""
    orch = AgentOrchestrator(config_file)

    async def fake_dispatch(tc):
        return (
            "Error rates in LLM agents are discussed with concrete mitigation "
            "strategies and benchmarks."
        )

    monkeypatch.setattr(orch, "_dispatch_tool_call", fake_dispatch)
    responses = [
        _tool_response("c1", "execute_skill", {"skill_name": "summarize"}),
        _text_response(""),
    ]
    call_idx = {"i": 0}

    async def fake_send(_request):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    orch._router.send = fake_send

    result = await orch.process_cron_message("이 문서 요약해줘")

    assert "확인한 근거는 있지만" in result
    assert "Error rates in LLM agents" in result
    assert "확인 중 오류" not in result
