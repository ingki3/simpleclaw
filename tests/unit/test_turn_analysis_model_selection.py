"""BIZ-453 — TurnAnalysis 전용 provider/model 선택 및 reasoning hint 배선 테스트.

llm.default 가 DeepSeek(OpenRouter) 여도 TurnAnalysis 는 config 의
``agent.turn_analysis.provider/model`` 로 지정한 Gemini 3.5 Flash 가상 백엔드를
쓰는지, 설정이 없으면 기존 default backend 동작을 유지하는지, 실패 시
retry_provider/retry_model → retry_backend → llm.fallback 순서로 재시도하는지
검증한다. 마지막으로 live 사고("금요일에 주말 날씨 확인" 을 즉시 날씨 질의로
축소) 재발 방지 regression 을 고정한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.turn_analysis import TurnAnalysis, analyze_turn_with_llm
from simpleclaw.llm.models import LLMResponse


def _write_config(tmp_path, turn_analysis_block: str) -> object:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
llm:
  default: openrouter_deepseek
  fallback: gemini
  providers:
    openrouter_deepseek:
      type: api
      provider: openai
      model: deepseek/deepseek-chat-v4
      api_key: test-key
      base_url: https://openrouter.ai/api/v1
    gemini:
      type: api
      model: gemini-2.0-flash
      api_key: test-key
agent:
  history_limit: 8
  db_path: "{tmp_path}/conversations.db"
  max_tool_iterations: 2
  turn_analysis:
{turn_analysis_block}
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
""",
        encoding="utf-8",
    )
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text(
        "# Agent\nYou are SimpleClaw.", encoding="utf-8"
    )
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


_ANALYSIS = TurnAnalysis(
    original_text="hello",
    normalized_question="hello",
    confidence=0.9,
)


@pytest.mark.asyncio
async def test_dedicated_provider_model_used_independently_of_default(
    tmp_path, monkeypatch
):
    """provider+model 설정 시 llm.default(DeepSeek)와 무관하게 Gemini 3.5 Flash 사용."""
    cfg = _write_config(
        tmp_path,
        """\
    enabled: true
    provider: gemini
    model: gemini-3.5-flash
    reasoning:
      enabled: true
      effort: medium
      budget_tokens: 512
""",
    )
    orch = AgentOrchestrator(cfg)
    analyzer = AsyncMock(return_value=_ANALYSIS)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )
    orch._tool_loop = AsyncMock(return_value="ok")

    await orch.process_message("hello", user_id=1, chat_id=1)

    kwargs = analyzer.await_args.kwargs
    # 최종 답변 default 는 계속 DeepSeek 백엔드다.
    assert orch._router.get_default_backend() == "openrouter_deepseek"
    # TurnAnalysis 는 전용 가상 백엔드(gemini credentials + 3.5 flash 모델)로 간다.
    assert kwargs["backend_name"] == "gemini#gemini-3.5-flash"
    virtual = orch._router._providers["gemini#gemini-3.5-flash"]
    assert virtual._model == "gemini-3.5-flash"
    # reasoning hint 도 함께 전달된다.
    assert kwargs["reasoning"] == {
        "enabled": True,
        "effort": "medium",
        "budget_tokens": 512,
    }
    # retry 미지정 → llm.fallback 백엔드에 위임.
    assert kwargs["retry_backend_name"] == "gemini"


@pytest.mark.asyncio
async def test_unset_provider_model_preserves_default_backend_behavior(
    tmp_path, monkeypatch
):
    """provider/model 미설정이면 기존처럼 backend(None → llm.default)로 간다."""
    cfg = _write_config(tmp_path, "    enabled: true\n")
    orch = AgentOrchestrator(cfg)
    analyzer = AsyncMock(return_value=_ANALYSIS)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )
    orch._tool_loop = AsyncMock(return_value="ok")

    await orch.process_message("hello", user_id=1, chat_id=1)

    kwargs = analyzer.await_args.kwargs
    assert kwargs["backend_name"] is None
    # 가상 백엔드가 등록되지 않는다.
    assert all("#" not in name for name in orch._router.list_backends())


@pytest.mark.asyncio
async def test_retry_provider_model_resolved_for_retry_path(tmp_path, monkeypatch):
    """retry_provider+retry_model 은 retry_backend/llm.fallback 보다 우선한다."""
    cfg = _write_config(
        tmp_path,
        """\
    enabled: true
    provider: gemini
    model: gemini-3.5-flash
    retry_provider: gemini
    retry_model: gemini-2.5-flash
    retry_backend: openrouter_deepseek
""",
    )
    orch = AgentOrchestrator(cfg)
    analyzer = AsyncMock(return_value=_ANALYSIS)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )
    orch._tool_loop = AsyncMock(return_value="ok")

    await orch.process_message("hello", user_id=1, chat_id=1)

    kwargs = analyzer.await_args.kwargs
    assert kwargs["backend_name"] == "gemini#gemini-3.5-flash"
    assert kwargs["retry_backend_name"] == "gemini#gemini-2.5-flash"


@pytest.mark.asyncio
async def test_unavailable_dedicated_provider_falls_back_to_backend_setting(
    tmp_path, monkeypatch
):
    """provider 해석 실패 시 다음 우선순위(backend)로 조용히 내려간다."""
    cfg = _write_config(
        tmp_path,
        """\
    enabled: true
    provider: not_configured_provider
    model: some-model
    backend: gemini
""",
    )
    orch = AgentOrchestrator(cfg)
    analyzer = AsyncMock(return_value=_ANALYSIS)
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm", analyzer
    )
    orch._tool_loop = AsyncMock(return_value="ok")

    await orch.process_message("hello", user_id=1, chat_id=1)

    assert analyzer.await_args.kwargs["backend_name"] == "gemini"


class _CapturingRouter:
    """analyze_turn_with_llm 요청 검증용 fake router."""

    def __init__(self, responses_by_backend: dict):
        self.requests = []
        self._responses = responses_by_backend

    async def send(self, request):
        self.requests.append(request)
        result = self._responses[request.backend_name]
        if isinstance(result, Exception):
            raise result
        return LLMResponse(text=result, backend_name=request.backend_name or "")


def _payload(**overrides) -> str:
    data = {
        "is_followup": False,
        "normalized_question": "q",
        "context_summary": "",
        "confidence": 0.9,
        "needs_clarification": False,
        "ambiguity_options": [],
        "domains": [],
        "intents": [],
        "route": "standard_tool_loop",
        "complexity_score": 1,
        "needs_current_facts": False,
        "needs_rules": False,
        "needs_remaining_variables": False,
        "needs_calculation": False,
        "needs_comparison_or_conditions": False,
        "needs_conflict_resolution": False,
        "needs_impact_analysis": False,
        "reasons": [],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


@pytest.mark.asyncio
async def test_reasoning_hint_attached_to_request_only_when_enabled():
    router = _CapturingRouter({"gemini#gemini-3.5-flash": _payload()})

    await analyze_turn_with_llm(
        "hello",
        recent_messages=[],
        router=router,
        backend_name="gemini#gemini-3.5-flash",
        reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
    )
    await analyze_turn_with_llm(
        "hello",
        recent_messages=[],
        router=router,
        backend_name="gemini#gemini-3.5-flash",
        reasoning={"enabled": False, "effort": "medium", "budget_tokens": 512},
    )

    assert router.requests[0].reasoning == {
        "enabled": True,
        "effort": "medium",
        "budget_tokens": 512,
    }
    assert router.requests[1].reasoning is None


@pytest.mark.asyncio
async def test_dedicated_model_failure_retries_with_retry_model():
    """전용 모델 호출 실패 시 retry 백엔드로 1회 재시도해 llm 결과를 살린다."""
    router = _CapturingRouter(
        {
            "gemini#gemini-3.5-flash": RuntimeError("provider down"),
            "gemini#gemini-2.5-flash": _payload(normalized_question="retried"),
        }
    )

    analysis = await analyze_turn_with_llm(
        "hello",
        recent_messages=[],
        router=router,
        backend_name="gemini#gemini-3.5-flash",
        retry_backend_name="gemini#gemini-2.5-flash",
    )

    assert analysis.source == "llm"
    assert analysis.normalized_question == "retried"
    assert [r.backend_name for r in router.requests] == [
        "gemini#gemini-3.5-flash",
        "gemini#gemini-2.5-flash",
    ]


@pytest.mark.asyncio
async def test_friday_weekend_weather_reminder_normalization_regression():
    """BIZ-453 live regression — "금요일에 주말 날씨 확인" 은 reminder/schedule
    성격을 보존해야 하며 즉시 "금요일 날씨" 질의로 축소되면 안 된다.

    live 대화에서 사용자가 두 번 정정했는데도 bot 이 금요일 예보 답변을
    반복한 사고를 고정한다. 전용 TurnAnalysis 모델(schema-compliant 응답)이
    내린 reminder 정규화가 파서/sanitize 단계에서 즉시 날씨 질의로 뭉개지지
    않는지 검증한다.
    """
    recent = [
        {"role": "user", "content": "주말 날씨 알려줘"},
        {"role": "assistant", "content": "이번 주말은 맑고 최고 27도예요."},
        {
            "role": "user",
            "content": "아. 금요일에 날씨를 한 번 더 알려 달라는 이야기야. "
            "금요일 날씨가 궁금한게 아니고.",
        },
        {"role": "assistant", "content": "금요일 날씨는 흐리고 비 소식이 있어요."},
    ]
    normalized = (
        "금요일에 주말 날씨 예보를 다시 확인해서 알려주는 리마인더를 등록해달라는 요청"
    )
    router = _CapturingRouter(
        {
            "gemini#gemini-3.5-flash": _payload(
                is_followup=True,
                normalized_question=normalized,
                domains=["weather", "productivity"],
                intents=["reminder", "schedule", "weather"],
                confidence=0.9,
            )
        }
    )

    analysis = await analyze_turn_with_llm(
        "금요일에 주말 날씨를 확인해 달라는 이야기야.",
        recent_messages=recent,
        router=router,
        backend_name="gemini#gemini-3.5-flash",
        reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
    )

    assert analysis.source == "llm"
    # reminder/schedule 의도가 보존된다 — 즉시 날씨 lookup 으로 축소되지 않는다.
    assert "reminder" in analysis.intents
    assert "schedule" in analysis.intents
    assert "금요일" in analysis.normalized_question
    assert "주말 날씨" in analysis.normalized_question
    assert "리마인더" in analysis.normalized_question
    assert analysis.normalized_question != "금요일 날씨"
    # 원문은 저장/감사용으로 그대로 유지된다.
    assert analysis.original_text == "금요일에 주말 날씨를 확인해 달라는 이야기야."
    # 두 번 정정한 맥락이 follow-up 으로 인식된다.
    assert analysis.is_followup is True
