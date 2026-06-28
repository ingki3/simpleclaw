from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import LLMResponse


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

    assert result == "근거 기반 현재 날씨"


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
