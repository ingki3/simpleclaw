"""BIZ-426 — orchestrator 의 LLM turn analysis primary path 배선 테스트.

일반 사용자 turn 에서 LLM TurnAnalysis 가 정규화 질문/clarify/intents/
domains/route 를 결정하고, 분석 비활성·실패 시에만 기존 결정적(keyword)
경로가 fallback 으로 동작하는지 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.response_router import ResponseRoute
from simpleclaw.agent.turn_analysis import TurnAnalysis
from simpleclaw.llm.models import LLMResponse
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-2.0-flash
      api_key: test-key
agent:
  history_limit: 8
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 2
  turn_analysis:
    enabled: true
    backend: gemini
    max_tokens: 256
    max_recent_messages: 8
  complex_fact_workflow:
    enabled: false
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
  token_budget: 4096
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: AGENT.md
      type: agent
memory:
  rag:
    enabled: false
""", encoding="utf-8")
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.", encoding="utf-8")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


def _enable_complex_workflow(config_file) -> None:
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace(
            "complex_fact_workflow:\n    enabled: false",
            "complex_fact_workflow:\n    enabled: true",
        ),
        encoding="utf-8",
    )


def _seed_turn(orch, user_text: str, assistant_text: str) -> None:
    orch._store.add_message(
        ConversationMessage(role=MessageRole.USER, content=user_text)
    )
    orch._store.add_message(
        ConversationMessage(role=MessageRole.ASSISTANT, content=assistant_text)
    )


@pytest.mark.asyncio
async def test_orchestrator_uses_llm_normalized_question_for_tool_loop(
    config_file, monkeypatch
):
    """LLM 이 정규화한 질문이 tool loop 실행 입력으로 전달된다."""
    orch = AgentOrchestrator(config_file)
    analyzer = AsyncMock(
        return_value=TurnAnalysis(
            original_text="그럼 현재 순위는?",
            normalized_question="직전 롯데 야구 맥락에서 현재 KBO 순위를 알려줘",
            is_followup=True,
            confidence=0.9,
            domains=("sports",),
            intents=("standings", "realtime_lookup"),
            route=ResponseRoute.CURRENT_FACT_GUARDED_LOOP,
            needs_current_facts=True,
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )

    captured = {}

    async def fake_tool_loop(text, **kwargs):
        captured["text"] = text
        return "순위 답변"

    orch._tool_loop = fake_tool_loop

    result = await orch.process_message("그럼 현재 순위는?", user_id=1, chat_id=1)

    assert result == "순위 답변"
    assert captured["text"] == "직전 롯데 야구 맥락에서 현재 KBO 순위를 알려줘"
    analyzer.assert_awaited_once()
    # 분석기에 설정값(backend/max_tokens)과 최근 대화가 전달된다.
    kwargs = analyzer.call_args.kwargs
    assert kwargs["backend_name"] == "gemini"
    assert kwargs["max_tokens"] == 256
    assert kwargs["max_recent_messages"] == 8
    # BIZ-427 — config 에 structured_output 미지정이어도 기본 True 로 전달된다.
    assert kwargs["structured_output"] is True


@pytest.mark.asyncio
async def test_original_text_is_saved_not_normalized_question(
    config_file, monkeypatch
):
    """DB 저장은 항상 사용자 원문 — LLM 정규화 질문은 실행 전용이다."""
    orch = AgentOrchestrator(config_file)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="그럼 현재 순위는?",
                normalized_question="직전 롯데 야구 맥락에서 현재 KBO 순위를 알려줘",
                confidence=0.9,
            )
        ),
    )

    async def fake_tool_loop(text, **kwargs):
        return "순위 답변"

    orch._tool_loop = fake_tool_loop

    await orch.process_message("그럼 현재 순위는?", user_id=1, chat_id=1)

    saved_user_texts = [
        msg.content
        for msg in orch._store.get_recent(limit=10)
        if msg.role == MessageRole.USER
    ]
    assert "그럼 현재 순위는?" in saved_user_texts
    assert all("KBO" not in text for text in saved_user_texts)


@pytest.mark.asyncio
async def test_orchestrator_clarifies_when_llm_analysis_is_ambiguous(
    config_file, monkeypatch
):
    """LLM 이 ambiguity 를 보고하면 기존 clarify UX 로 사용자에게 되묻는다."""
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="실행되면 안 됨"))
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="그거 다시 확인해줘",
                normalized_question="그거 다시 확인해줘",
                confidence=0.4,
                needs_clarification=True,
                ambiguity_options=["롯데 경기 결과", "agent-study-daily 실패"],
            )
        ),
    )

    result = await orch.process_message("그거 다시 확인해줘", user_id=1, chat_id=7)

    # tool loop 실행 없이 clarify 질문 + 번호 옵션이 돌아와야 한다.
    orch._router.send.assert_not_called()
    assert "어느 맥락" in result
    assert "롯데 경기 결과" in result
    assert "agent-study-daily 실패" in result
    pending = orch.pop_pending_clarify(7)
    assert pending is not None
    assert len(pending.options) >= 2


@pytest.mark.asyncio
async def test_llm_turn_analysis_can_route_complex_workflow(config_file, monkeypatch):
    """LLM route=COMPLEX_FACT_WORKFLOW 가 complex workflow 를 호출한다."""
    _enable_complex_workflow(config_file)
    orch = AgentOrchestrator(config_file)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="그럼 경우의 수는?",
                normalized_question=(
                    "직전 월드컵 조별리그 맥락에서 한국의 16강 진출 경우의 수를 계산해줘"
                ),
                is_followup=True,
                confidence=0.91,
                domains=("sports",),
                intents=("scenario_analysis",),
                route=ResponseRoute.COMPLEX_FACT_WORKFLOW,
                complexity_score=5,
                needs_current_facts=True,
                needs_rules=True,
                needs_remaining_variables=True,
                needs_calculation=True,
                needs_comparison_or_conditions=True,
                reasons=("requires standings, remaining matches, and rules",),
            )
        ),
    )
    orch._run_complex_fact_workflow = AsyncMock(return_value="경우의 수 답변")

    result = await orch.process_message("그럼 경우의 수는?", user_id=1, chat_id=1)

    assert result == "경우의 수 답변"
    orch._run_complex_fact_workflow.assert_awaited_once()
    assert "16강 진출 경우의 수" in orch._run_complex_fact_workflow.call_args.args[0]


@pytest.mark.asyncio
async def test_primary_route_does_not_require_keyword_cues(config_file, monkeypatch):
    """keyword cue 가 없어도 LLM intents/domains/route 로 primary path 가 동작한다."""
    orch = AgentOrchestrator(config_file)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="그 표 좀 보여줘",
                normalized_question="직전 KBO 대화 맥락에서 현재 리그 순위표를 보여줘",
                is_followup=True,
                confidence=0.88,
                domains=("sports",),
                intents=("standings",),
                route=ResponseRoute.CURRENT_FACT_GUARDED_LOOP,
                needs_current_facts=True,
            )
        ),
    )

    captured = {}

    async def fake_tool_loop(text, **kwargs):
        captured["text"] = text
        return "표 답변"

    orch._tool_loop = fake_tool_loop

    result = await orch.process_message("그 표 좀 보여줘", user_id=1, chat_id=1)

    assert result == "표 답변"
    assert captured["text"] == "직전 KBO 대화 맥락에서 현재 리그 순위표를 보여줘"


@pytest.mark.asyncio
async def test_llm_intents_reach_capability_router(config_file, monkeypatch):
    """LLM 제공 intents/domains 가 select_capability 에 explicit 으로 전달된다."""
    orch = AgentOrchestrator(config_file)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="그 표 좀 보여줘",
                normalized_question="현재 리그 순위표를 보여줘",
                confidence=0.9,
                domains=("sports",),
                intents=("standings",),
            )
        ),
    )
    captured = {}

    def fake_select(route_input, **kwargs):
        captured["explicit_intents"] = kwargs.get("explicit_intents")
        captured["explicit_domains"] = kwargs.get("explicit_domains")
        return None

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.select_capability", fake_select
    )

    async def fake_tool_loop(text, **kwargs):
        return "답변"

    orch._tool_loop = fake_tool_loop

    await orch.process_message("그 표 좀 보여줘", user_id=1, chat_id=1)

    assert captured["explicit_intents"] == ("standings",)
    assert captured["explicit_domains"] == ("sports",)


@pytest.mark.asyncio
async def test_analyzer_fallback_uses_deterministic_route_path(
    config_file, monkeypatch
):
    """분석기 실패(source=fallback) 시 기존 결정적 keyword 경로가 유지된다."""
    orch = AgentOrchestrator(config_file)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="그럼 현재 순위는?",
                normalized_question="그럼 현재 순위는?",
                confidence=0.0,
                reasons=("turn_analysis_fallback",),
                source="fallback",
            )
        ),
    )
    _seed_turn(orch, "오늘 롯데 야구 어떻게 되었지?", "롯데가 KT에 패했습니다.")

    captured = {}

    def fake_classify(text, *args, **kwargs):
        captured["text"] = text
        from simpleclaw.agent.response_router import RouteDecision

        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["test"])

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.classify_response_route", fake_classify
    )

    async def fake_tool_loop(text, **kwargs):
        return "답변"

    orch._tool_loop = fake_tool_loop

    await orch.process_message("그럼 현재 순위는?", user_id=1, chat_id=1)

    # 결정적 TurnFrame 정규화 + classify_response_route 가 fallback 으로 동작.
    assert "그럼 현재 순위는?" in captured["text"]
    assert "롯데" in captured["text"]


@pytest.mark.asyncio
async def test_disabled_turn_analysis_skips_llm_analyzer(config_file, monkeypatch):
    """turn_analysis.enabled=false 면 LLM 분석기를 호출하지 않는다."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace("turn_analysis:\n    enabled: true", "turn_analysis:\n    enabled: false"),
        encoding="utf-8",
    )
    orch = AgentOrchestrator(config_file)
    analyzer = AsyncMock()
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )

    async def fake_tool_loop(text, **kwargs):
        return "답변"

    orch._tool_loop = fake_tool_loop

    result = await orch.process_message("안녕?", user_id=1, chat_id=1)

    assert result == "답변"
    analyzer.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_analysis_decisions_are_logged_without_user_text(
    config_file, monkeypatch, caplog
):
    """TurnAnalysis/route decision 이 로그에 남되 사용자 발화 전문은 남지 않는다."""
    orch = AgentOrchestrator(config_file)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="다음 주 부산 출장 일정 정리해줘",
                normalized_question="다음 주 부산 출장 일정 정리해줘",
                confidence=0.95,
            )
        ),
    )

    async def fake_tool_loop(text, **kwargs):
        return "답변"

    orch._tool_loop = fake_tool_loop

    with caplog.at_level("INFO", logger="simpleclaw.agent.orchestrator"):
        await orch.process_message("다음 주 부산 출장 일정 정리해줘", user_id=1, chat_id=1)

    assert "TurnAnalysis built:" in caplog.text
    assert "Route decision:" in caplog.text
    assert "부산 출장" not in caplog.text


@pytest.mark.asyncio
async def test_structured_output_config_false_reaches_analyzer(
    config_file, monkeypatch
):
    """BIZ-427 — config 의 structured_output: false 가 분석기 인자로 전달된다."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace(
            "max_recent_messages: 8",
            "max_recent_messages: 8\n    structured_output: false",
        ),
        encoding="utf-8",
    )
    orch = AgentOrchestrator(config_file)
    analyzer = AsyncMock(
        return_value=TurnAnalysis(original_text="안녕", normalized_question="안녕")
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )
    orch._tool_loop = AsyncMock(return_value="안녕하세요")

    await orch.process_message("안녕", user_id=1, chat_id=1)

    assert analyzer.call_args.kwargs["structured_output"] is False
