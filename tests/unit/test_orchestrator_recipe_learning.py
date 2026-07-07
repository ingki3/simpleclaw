"""orchestrator recipe learning capture hook 단위 테스트 (BIZ-428)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_loop import ToolLoopResult, ToolTraceStep
from simpleclaw.recipes.learning import (
    RECIPE_SUGGESTION_RESPONSE_SCHEMA,
    RecipeSuggestionStore,
)


def _write_config(tmp_path, *, learning: str = "") -> object:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"recipes:\n  dir: {tmp_path / 'recipes'}\n{learning}", encoding="utf-8"
    )
    return config


def _enabled_learning_block(tmp_path, extra: str = "") -> str:
    return (
        "  learning:\n"
        "    enabled: true\n"
        f"    suggestions_file: {tmp_path / 'recipe_suggestions.jsonl'}\n"
        f"{extra}"
    )


def _orchestrator(tmp_path, monkeypatch, *, learning: str = "") -> AgentOrchestrator:
    config = _write_config(tmp_path, learning=learning)
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orchestrator = AgentOrchestrator(config)
    orchestrator._router = MagicMock()
    return orchestrator


def _candidate_payload() -> dict:
    return {
        "title": "아침 브리핑",
        "rationale": "매일 반복되는 조회 절차",
        "recipe_name": "morning-briefing",
        "description": "아침 뉴스/날씨 요약",
        "trigger": "아침 브리핑",
        "instructions": "{{ city }} 날씨와 주요 뉴스를 요약해줘.",
        "required_skills": [],
        "parameters": [
            {"name": "city", "description": "도시", "required": False, "default": "서울"}
        ],
        "cron_hint": "0 8 * * *",
        "risk_flags": [],
    }


def _complex_result() -> ToolLoopResult:
    trace = [
        ToolTraceStep(tool_name="web_search", arguments={"q": "날씨"}, observation_preview="ok"),
        ToolTraceStep(tool_name="web_fetch", arguments={"url": "https://x"}, observation_preview="ok"),
    ]
    return ToolLoopResult(text="x" * 600, trace=trace, iterations=2, success=True)


@pytest.mark.asyncio
async def test_disabled_default_captures_nothing(tmp_path, monkeypatch):
    """recipes.learning 미설정(disabled 기본값)이면 hook은 LLM 호출 없이 종료한다."""
    orchestrator = _orchestrator(tmp_path, monkeypatch)
    orchestrator._router.send = AsyncMock()

    await orchestrator._capture_recipe_learning_candidate(
        "user", "x" * 600, _complex_result(), [1, 2]
    )

    assert orchestrator._recipe_learning_config["enabled"] is False
    orchestrator._router.send.assert_not_awaited()
    assert not (tmp_path / "recipe_suggestions.jsonl").exists()


@pytest.mark.asyncio
async def test_successful_complex_trace_persists_pending_suggestion(
    tmp_path, monkeypatch
):
    orchestrator = _orchestrator(
        tmp_path, monkeypatch, learning=_enabled_learning_block(tmp_path)
    )
    response = MagicMock()
    response.text = json.dumps(_candidate_payload(), ensure_ascii=False)
    orchestrator._router.send = AsyncMock(return_value=response)

    await orchestrator._capture_recipe_learning_candidate(
        "아침마다 브리핑해줘", "x" * 600, _complex_result(), [1, 2]
    )

    items = RecipeSuggestionStore(tmp_path / "recipe_suggestions.jsonl").list_pending()
    assert len(items) == 1
    suggestion = items[0]
    assert suggestion.recipe_name == "morning-briefing"
    assert suggestion.status == "pending"
    assert suggestion.cron_hint == "0 8 * * *"
    assert suggestion.source_msg_ids == [1, 2]
    assert suggestion.trace


@pytest.mark.asyncio
async def test_capture_uses_structured_output_schema(tmp_path, monkeypatch):
    """BIZ-427 — structured_output 게이트가 켜져 있으면 response_schema를 강제한다."""
    orchestrator = _orchestrator(
        tmp_path, monkeypatch, learning=_enabled_learning_block(tmp_path)
    )
    response = MagicMock()
    response.text = json.dumps(_candidate_payload(), ensure_ascii=False)
    orchestrator._router.send = AsyncMock(return_value=response)

    await orchestrator._capture_recipe_learning_candidate(
        "user", "x" * 600, _complex_result(), [1]
    )

    request = orchestrator._router.send.call_args.args[0]
    assert request.response_mime_type == "application/json"
    assert request.response_schema is RECIPE_SUGGESTION_RESPONSE_SCHEMA
    assert request.require_structured_output is True


@pytest.mark.asyncio
async def test_capture_skips_schema_when_structured_output_disabled(
    tmp_path, monkeypatch
):
    orchestrator = _orchestrator(
        tmp_path,
        monkeypatch,
        learning=_enabled_learning_block(tmp_path, "    structured_output: false\n"),
    )
    response = MagicMock()
    response.text = json.dumps(_candidate_payload(), ensure_ascii=False)
    orchestrator._router.send = AsyncMock(return_value=response)

    await orchestrator._capture_recipe_learning_candidate(
        "user", "x" * 600, _complex_result(), [1]
    )

    request = orchestrator._router.send.call_args.args[0]
    assert request.response_schema is None
    assert request.require_structured_output is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        # 실패 trace — success=False
        ToolLoopResult(text="x" * 600, trace=[
            ToolTraceStep(tool_name="web_search", arguments={}, observation_preview="ok"),
            ToolTraceStep(tool_name="web_fetch", arguments={}, observation_preview="ok"),
        ], success=False),
        # 도구 수 부족 — distinct 1개
        ToolLoopResult(text="x" * 600, trace=[
            ToolTraceStep(tool_name="web_search", arguments={}, observation_preview="ok"),
            ToolTraceStep(tool_name="web_search", arguments={}, observation_preview="ok"),
        ], success=True),
        # 짧은 최종 답변
        ToolLoopResult(text="short", trace=[
            ToolTraceStep(tool_name="web_search", arguments={}, observation_preview="ok"),
            ToolTraceStep(tool_name="web_fetch", arguments={}, observation_preview="ok"),
        ], success=True),
    ],
)
async def test_non_qualifying_traces_create_no_candidate(
    tmp_path, monkeypatch, result
):
    orchestrator = _orchestrator(
        tmp_path, monkeypatch, learning=_enabled_learning_block(tmp_path)
    )
    orchestrator._router.send = AsyncMock()

    await orchestrator._capture_recipe_learning_candidate(
        "user", result.text, result, [1]
    )

    orchestrator._router.send.assert_not_awaited()
    assert not (tmp_path / "recipe_suggestions.jsonl").exists()


@pytest.mark.asyncio
async def test_llm_failure_does_not_break_user_response(tmp_path, monkeypatch):
    """LLM/schema 실패는 warning 로그만 남기고 예외를 전파하지 않는다."""
    orchestrator = _orchestrator(
        tmp_path, monkeypatch, learning=_enabled_learning_block(tmp_path)
    )
    orchestrator._router.send = AsyncMock(side_effect=RuntimeError("provider down"))

    await orchestrator._capture_recipe_learning_candidate(
        "user", "x" * 600, _complex_result(), [1]
    )

    assert not (tmp_path / "recipe_suggestions.jsonl").exists()


@pytest.mark.asyncio
async def test_schema_parse_failure_does_not_break_user_response(tmp_path, monkeypatch):
    orchestrator = _orchestrator(
        tmp_path, monkeypatch, learning=_enabled_learning_block(tmp_path)
    )
    response = MagicMock()
    response.text = "not-json"
    orchestrator._router.send = AsyncMock(return_value=response)

    await orchestrator._capture_recipe_learning_candidate(
        "user", "x" * 600, _complex_result(), [1]
    )

    assert not (tmp_path / "recipe_suggestions.jsonl").exists()
