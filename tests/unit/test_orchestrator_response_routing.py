from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import LLMResponse
from simpleclaw.memory.models import ConversationMessage, MessageRole


def _seed_turn(orch, user_text: str, assistant_text: str) -> None:
    """대화 저장소에 user/assistant 한 turn 을 직접 적재한다."""
    orch._store.add_message(
        ConversationMessage(role=MessageRole.USER, content=user_text)
    )
    orch._store.add_message(
        ConversationMessage(role=MessageRole.ASSISTANT, content=assistant_text)
    )


def _write_capability_skill(config_file) -> None:
    """read-only standings capability 를 선언한 스킬을 local_skills 에 만든다."""
    skills_dir = config_file.parent / "local_skills" / "sports-lookup-skill"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "SKILL.md").write_text(
        """---
name: sports-lookup-skill
description: 스포츠 리그 순위/경기 결과 실시간 조회.
capability:
  domains: [sports]
  intents: [standings, current_result]
  read_only: true
  side_effects: false
  freshness_sensitive: true
---
# Sports Lookup Skill
""",
        encoding="utf-8",
    )


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
  history_limit: 3
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 2
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


@pytest.mark.asyncio
async def test_disabled_complex_workflow_falls_back_to_tool_loop(config_file):
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="기존 루프 답변"))

    result = await orch.process_message("한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘", 1, 1)

    assert result == "기존 루프 답변"
    assert orch._router.send.await_count == 1


@pytest.mark.asyncio
async def test_smalltalk_does_not_enter_complex_workflow_when_enabled(config_file):
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="안녕하세요 형님."))

    result = await orch.process_message("안녕?", 1, 1)

    assert result == "안녕하세요 형님."
    assert orch._router.send.await_count == 1


@pytest.mark.asyncio
async def test_enabled_complex_question_uses_complex_workflow(config_file, monkeypatch):
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="should not be direct loop"))

    called = {"value": False}

    async def fake_complex(text, decision, *, on_progress=None):
        called["value"] = True
        assert decision.route.value == "complex_fact_workflow"
        return "복합 워크플로우 답변"

    monkeypatch.setattr(orch, "_run_complex_fact_workflow", fake_complex)

    result = await orch.process_message("한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘", 1, 1)

    assert result == "복합 워크플로우 답변"
    assert called["value"] is True
    orch._router.send.assert_not_called()


@pytest.mark.asyncio
async def test_current_fact_question_does_not_use_complex_workflow_when_enabled(config_file, monkeypatch):
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="근거 기반 현재 날씨"))

    async def fail_complex(*args, **kwargs):
        raise AssertionError("current single fact should not use complex workflow")

    monkeypatch.setattr(orch, "_run_complex_fact_workflow", fail_complex)

    result = await orch.process_message("오늘 서울 날씨 어때?", 1, 1)

    # 라우팅은 complex workflow 로 가지 않고 standard tool loop 로 처리돼야 한다
    # (fail_complex 가 호출되면 AssertionError). 단, 실시간 사실 질의인데 최신
    # 근거 없이 만든 final answer 는 BIZ-363 가드가 차단한다.
    assert "확인하지 못" in result
    assert "근거 기반 현재 날씨" not in result


@pytest.mark.asyncio
async def test_world_cup_scenario_uses_complex_workflow_when_enabled(config_file, monkeypatch):
    """BIZ-394: 월드컵 경우의 수(현재성+규칙+남은 변수) 질문은 complex workflow로 간다."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="should not be direct loop"))

    captured = {}

    async def fake_complex(text, decision, *, on_progress=None):
        captured["decision"] = decision
        return "복합 워크플로우 답변"

    monkeypatch.setattr(orch, "_run_complex_fact_workflow", fake_complex)

    result = await orch.process_message("대한민국 월드컵 32강 진출 가능성이 어떻게 되지?", 1, 1)

    assert result == "복합 워크플로우 답변"
    decision = captured["decision"]
    assert decision.route.value == "complex_fact_workflow"
    assert decision.needs_current_facts is True


@pytest.mark.asyncio
async def test_market_impact_question_does_not_use_complex_but_not_standard(
    config_file, monkeypatch
):
    """BIZ-394: 시장 영향 분석 질문은 complex까지는 아니어도 standard default로 안 떨어진다."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="근거 기반 답변"))

    async def fail_complex(*args, **kwargs):
        raise AssertionError("market impact question should not use complex workflow")

    monkeypatch.setattr(orch, "_run_complex_fact_workflow", fail_complex)

    from simpleclaw.agent.response_router import ResponseRoute, classify_response_route

    decision = classify_response_route(
        "OpenAI 상장 연기가 증시에 끼치는 영향을 조사해줘", route_threshold=3
    )
    assert decision.route != ResponseRoute.STANDARD_TOOL_LOOP
    assert decision.needs_impact_analysis is True

    result = await orch.process_message(
        "OpenAI 상장 연기가 증시에 끼치는 영향을 조사해줘", 1, 1
    )
    # complex workflow 로 가지 않는다는 라우팅 특성은 fail_complex 로 검증되고,
    # 최신 근거 없이 만든 실시간 사실 final answer 는 BIZ-363 가드가 차단한다.
    assert "확인하지 못" in result
    assert "근거 기반 답변" not in result


@pytest.mark.asyncio
async def test_dspy_backend_setting_warns_and_falls_back(config_file, monkeypatch, caplog):
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(
        text.replace(
            "  complex_fact_workflow:\n    enabled: false",
            "  complex_fact_workflow:\n    enabled: true\n    planner_backend: dspy",
        ),
        encoding="utf-8",
    )
    orch = AgentOrchestrator(config_file)

    class FakeRetriever:
        async def search_for_slot(self, slot_name, query):
            from simpleclaw.agent.fact_types import EvidenceCoverage, EvidenceItem

            return [EvidenceItem(
                source_url="https://official.example",
                source_type="official",
                claim=f"{slot_name} evidence",
                coverage=EvidenceCoverage.FINAL,
                confidence="high",
            )]

    monkeypatch.setattr(
        "simpleclaw.agent.evidence_retrieval.EvidenceRetriever",
        lambda **kwargs: FakeRetriever(),
    )
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="답변"))

    with caplog.at_level("WARNING"):
        result = await orch.process_message("한국이 아직 16강 갈 가능성 있어? 경우의 수 알려줘", 1, 1)

    assert result == "답변"
    assert "not implemented; falling back to simpleclaw" in caplog.text


# ----------------------------------------------------------------------
# BIZ-425 — TurnFrame 정규화 / ClarifyGate / capability preempt 배선 검증
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_routes_using_normalized_question_for_followup(
    config_file, monkeypatch
):
    """follow-up 은 raw text 가 아니라 맥락 복원된 정규화 질문으로 라우팅된다."""
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="순위 답변"))
    _seed_turn(orch, "오늘 롯데 야구 어떻게 되었지?", "롯데가 KT에 2대 4로 패했습니다.")

    captured = {}

    def fake_classify(text, *args, **kwargs):
        captured["text"] = text
        from simpleclaw.agent.response_router import ResponseRoute, RouteDecision

        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["test"])

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.classify_response_route", fake_classify
    )

    await orch.process_message("그럼 현재 순위는?", 1, 1)

    assert "그럼 현재 순위는?" in captured["text"]
    # 원문에는 없는 직전 맥락(롯데)이 라우팅 입력에 복원돼 있어야 한다.
    assert "롯데" in captured["text"]


@pytest.mark.asyncio
async def test_ordinary_question_routes_with_original_text(config_file, monkeypatch):
    """일반 질문은 최근 맥락이 있어도 원문 그대로 라우팅된다(동작 불변)."""
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="답변"))
    _seed_turn(orch, "오늘 롯데 야구 어떻게 되었지?", "롯데가 패했습니다.")

    captured = {}

    def fake_classify(text, *args, **kwargs):
        captured["text"] = text
        from simpleclaw.agent.response_router import ResponseRoute, RouteDecision

        return RouteDecision(ResponseRoute.STANDARD_TOOL_LOOP, 0, ["test"])

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.classify_response_route", fake_classify
    )

    await orch.process_message("다음 주 부산 출장 일정 정리해줘", 1, 1)

    assert captured["text"] == "다음 주 부산 출장 일정 정리해줘"


@pytest.mark.asyncio
async def test_ambiguous_followup_returns_clarify_request(config_file):
    """복수 맥락 후보 + 지시대명사형 follow-up 은 실행 대신 clarify 로 되묻는다."""
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="실행되면 안 됨"))
    # 두 맥락(4개 메시지)이 모두 프레임 윈도우에 들어오도록 히스토리를 넓힌다.
    orch._history_limit = 8
    _seed_turn(orch, "오늘 롯데 야구 어떻게 되었지?", "롯데가 패했습니다.")
    _seed_turn(orch, "agent-study-daily 왜 실패했어?", "레시피 timeout이 있었습니다.")

    result = await orch.process_message("그거 다시 확인해줘", 1, 7)

    # LLM 호출 없이 clarify 질문 + 번호 옵션이 돌아와야 한다.
    orch._router.send.assert_not_called()
    assert "어느 맥락 기준으로 이어서 확인할까요?" in result
    assert "1." in result and "2." in result
    pending = orch.pop_pending_clarify(7)
    assert pending is not None
    assert len(pending.options) >= 2


@pytest.mark.asyncio
async def test_read_only_capability_preempts_complex_fact_route(
    config_file, monkeypatch
):
    """read-only capability 후보가 있으면 조회성 질문은 complex 로 과승격되지 않는다."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    _write_capability_skill(config_file)
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="순위표 답변"))

    async def fail_complex(*args, **kwargs):
        raise AssertionError(
            "read-only capability matched question must not use complex workflow"
        )

    monkeypatch.setattr(orch, "_run_complex_fact_workflow", fail_complex)

    from simpleclaw.agent.response_router import ResponseRoute, classify_response_route

    question = "현재 야구 리그 순위표에서 롯데 순위 어떻게 되지?"
    # 전제: capability preempt 가 없으면 complex 로 승격될 모양의 질문이어야 한다.
    assert (
        classify_response_route(question, route_threshold=3).route
        == ResponseRoute.COMPLEX_FACT_WORKFLOW
    )

    result = await orch.process_message(question, 1, 1)

    # fail_complex 가 호출되지 않았고 tool loop 경로가 실행됐다.
    assert isinstance(result, str) and result
    assert orch._router.send.await_count >= 1


@pytest.mark.asyncio
async def test_scenario_question_still_uses_complex_despite_capability(
    config_file, monkeypatch
):
    """남은 변수(경우의 수)가 필요한 질문은 capability 후보가 있어도 complex 유지."""
    text = config_file.read_text(encoding="utf-8")
    config_file.write_text(text.replace("enabled: false", "enabled: true"), encoding="utf-8")
    _write_capability_skill(config_file)
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="직접 루프 금지"))

    called = {"value": False}

    async def fake_complex(text, decision, *, on_progress=None):
        called["value"] = True
        return "복합 워크플로우 답변"

    monkeypatch.setattr(orch, "_run_complex_fact_workflow", fake_complex)

    result = await orch.process_message(
        "롯데가 남은 경기에서 몇 승을 해야 3위 순위 가능성이 있어? 경우의 수 알려줘", 1, 1
    )

    assert called["value"] is True
    assert result == "복합 워크플로우 답변"


@pytest.mark.asyncio
async def test_routing_decisions_are_logged(config_file, caplog):
    """TurnFrame/route decision 이 structured log 로 남는다 (BIZ-425 관측성)."""
    orch = AgentOrchestrator(config_file)
    orch._router = MagicMock()
    orch._router.send = AsyncMock(return_value=LLMResponse(text="답변"))

    with caplog.at_level("INFO", logger="simpleclaw.agent.orchestrator"):
        await orch.process_message("다음 주 부산 출장 일정 정리해줘", 1, 1)

    assert "TurnFrame built:" in caplog.text
    assert "Route decision:" in caplog.text
    # 사용자 발화 전문은 로그에 남기지 않는다 (redacted/length only).
    assert "부산 출장" not in caplog.text
