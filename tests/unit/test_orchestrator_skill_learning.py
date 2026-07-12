"""BIZ-429 — orchestrator skill learning 후보 생성 파이프라인 테스트.

후보 LLM 출력이 BIZ-427 structured output(schema-constrained JSON)으로
강제되는지, 실패가 사용자 turn 을 깨지 않고 안전 진단만 남기는지,
후보 적재 시 운영자 알림 hook 이 호출되는지 검증한다.
"""

from __future__ import annotations

import json

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_loop import ToolLoopResult, ToolTraceStep
from simpleclaw.llm.models import LLMResponse
from simpleclaw.skills.learning import (
    SKILL_SUGGESTION_RESPONSE_SCHEMA,
    SkillSuggestionStore,
)

_VALID_SKILL_MD = (
    "---\nname: news-brief\ndescription: Summarize fresh news briefly.\n---\n"
    "# News Brief\n\n"
    "## When to Use\n- user asks for fresh news\n\n"
    "## Procedure\n1. web_search then summarize\n\n"
    "## Verification\n- answer cites sources\n"
)
_VALID_PAYLOAD = {
    "title": "News brief",
    "rationale": "Reusable search+summarize trace",
    "skill_name": "news-brief",
    "skill_md": _VALID_SKILL_MD,
    "scripts": [{"path": "scripts/run.py", "content": "print('ok')\n"}],
    "references": [],
    "risk_flags": ["network"],
}


def _config_file(tmp_path, *, enabled=True, structured_output=True):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
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
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
  learning:
    enabled: {str(enabled).lower()}
    min_tool_calls: 2
    min_distinct_tools: 2
    min_final_chars: 10
    structured_output: {str(structured_output).lower()}
    suggestions_file: "{tmp_path}/skill_suggestions.jsonl"
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
    persona.mkdir(exist_ok=True)
    (persona / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.", encoding="utf-8")
    (tmp_path / "local_skills").mkdir(exist_ok=True)
    (tmp_path / "global_skills").mkdir(exist_ok=True)
    return cfg


def _complex_result() -> ToolLoopResult:
    return ToolLoopResult(
        text="충분히 긴 최종 답변",
        trace=[
            ToolTraceStep(
                tool_name="web_search", arguments={}, observation_preview="ok"
            ),
            ToolTraceStep(
                tool_name="web_fetch", arguments={}, observation_preview="ok"
            ),
        ],
        success=True,
    )


@pytest.mark.asyncio
async def test_draft_uses_structured_output_request(tmp_path):
    """후보 LLM 요청이 BIZ-427 schema-constrained structured output 을 사용한다."""
    orch = AgentOrchestrator(_config_file(tmp_path))
    orch._router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(_VALID_PAYLOAD))
    )

    await orch._capture_skill_learning_candidate(
        "최신 뉴스 요약해줘", "충분히 긴 최종 답변", _complex_result(), [1, 2]
    )

    request = orch._router.send.await_args.args[0]
    assert request.response_mime_type == "application/json"
    assert request.response_schema is SKILL_SUGGESTION_RESPONSE_SCHEMA
    assert request.require_structured_output is True

    stored = SkillSuggestionStore(tmp_path / "skill_suggestions.jsonl").list_pending()
    assert len(stored) == 1
    assert stored[0].skill_name == "news-brief"
    assert stored[0].scripts == {"scripts/run.py": "print('ok')\n"}


@pytest.mark.asyncio
async def test_structured_output_can_be_disabled_by_config(tmp_path):
    orch = AgentOrchestrator(_config_file(tmp_path, structured_output=False))
    orch._router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(_VALID_PAYLOAD))
    )

    await orch._capture_skill_learning_candidate(
        "u", "충분히 긴 최종 답변", _complex_result(), [1]
    )

    request = orch._router.send.await_args.args[0]
    assert request.response_mime_type is None
    assert request.response_schema is None
    assert request.require_structured_output is False


@pytest.mark.asyncio
async def test_llm_failure_is_isolated_and_logs_safe_diagnostics(tmp_path, caplog):
    """LLM/schema 실패는 turn 을 깨지 않고, raw 전문 대신 안전 진단만 남긴다."""
    orch = AgentOrchestrator(_config_file(tmp_path))
    secret_raw = "RAW-USER-OBSERVATION sk-abcdefghijklmnopqrstuvwx"
    orch._router.send = AsyncMock(
        return_value=LLMResponse(text=secret_raw)  # JSON 이 아님 → 파싱 실패
    )

    with caplog.at_level("WARNING"):
        await orch._capture_skill_learning_candidate(
            "u", "충분히 긴 최종 답변", _complex_result(), [1]
        )

    assert not SkillSuggestionStore(
        tmp_path / "skill_suggestions.jsonl"
    ).list_pending()
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "raw_len" in warning
    assert "RAW-USER-OBSERVATION" not in warning


@pytest.mark.asyncio
async def test_disabled_config_skips_candidate_capture(tmp_path):
    orch = AgentOrchestrator(_config_file(tmp_path, enabled=False))
    orch._router.send = AsyncMock()

    await orch._capture_skill_learning_candidate(
        "u", "충분히 긴 최종 답변", _complex_result(), [1]
    )

    orch._router.send.assert_not_awaited()
    assert not (tmp_path / "skill_suggestions.jsonl").exists()


@pytest.mark.asyncio
async def test_simple_trace_does_not_create_candidate(tmp_path):
    orch = AgentOrchestrator(_config_file(tmp_path))
    orch._router.send = AsyncMock()

    simple = ToolLoopResult(
        text="답",
        trace=[
            ToolTraceStep(
                tool_name="web_search", arguments={}, observation_preview="ok"
            )
        ],
        success=True,
    )
    await orch._capture_skill_learning_candidate("u", "답", simple, [1])

    orch._router.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_capture_notifies_operator_hook(tmp_path, caplog):
    """후보 적재 성공 시 운영자 알림 hook 호출 + pending 이벤트 로그를 남긴다."""
    orch = AgentOrchestrator(_config_file(tmp_path))
    orch._router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(_VALID_PAYLOAD))
    )
    notifier = AsyncMock()
    orch._skill_candidate_notifier = notifier

    with caplog.at_level("INFO"):
        await orch._capture_skill_learning_candidate(
            "u", "충분히 긴 최종 답변", _complex_result(), [1]
        )

    notifier.assert_awaited_once()
    notified = notifier.await_args.args[0]
    assert notified.skill_name == "news-brief"
    assert notified.status == "pending"
    assert any(
        "Skill suggestion pending operator review" in r.getMessage()
        for r in caplog.records
    )
