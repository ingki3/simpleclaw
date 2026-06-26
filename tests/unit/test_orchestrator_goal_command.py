from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_loop import ToolLoopResult
from simpleclaw.llm.models import LLMResponse


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n"
        "  default: gemini\n"
        "  providers:\n"
        "    gemini:\n"
        "      type: api\n"
        "      model: gemini-test\n"
        "      api_key: env:GOOGLE_API_KEY\n"
        "agent:\n"
        f"  db_path: {tmp_path / 'conversation.db'}\n"
        f"  workspace_dir: {tmp_path / 'workspace'}\n"
        "  asset_selection:\n"
        "    enabled: false\n"
        "memory:\n"
        "  rag:\n"
        "    enabled: false\n"
        "recipes:\n"
        f"  dir: {tmp_path / 'recipes'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    return config


@pytest.mark.asyncio
async def test_goal_help_returns_usage_without_llm(config_file: Path):
    orch = AgentOrchestrator(config_file)
    orch._router.send = AsyncMock()

    result = await orch.process_message("/goal", user_id=1, chat_id=1)

    assert "/goal <목표>" in result
    orch._router.send.assert_not_called()


@pytest.mark.asyncio
async def test_goal_command_runs_round_and_judge(config_file: Path):
    orch = AgentOrchestrator(config_file)
    orch._router.send = AsyncMock(
        side_effect=[
            LLMResponse(text="조사 결과입니다", tool_calls=None),
            LLMResponse(text='{"status":"done","reason":"충족","confidence":"high"}'),
        ]
    )

    result = await orch.process_message("/goal 테스트 목표", user_id=1, chat_id=1)

    assert "/goal 결과" in result
    assert "조사 결과입니다" in result
    assert orch._router.send.await_count == 2


@pytest.mark.asyncio
async def test_goal_command_does_not_dispatch_as_recipe(
    config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    orch = AgentOrchestrator(config_file)
    orch._router.send = AsyncMock(
        side_effect=[
            LLMResponse(text="answer", tool_calls=None),
            LLMResponse(text='{"status":"done","reason":"ok","confidence":"high"}'),
        ]
    )
    recipe_mock = AsyncMock(return_value=("bad", "goal"))
    monkeypatch.setattr("simpleclaw.agent.orchestrator.try_recipe_command", recipe_mock)

    result = await orch.process_message("/goal 목표", user_id=1, chat_id=1)

    assert "answer" in result
    recipe_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_goal_disabled_returns_message_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n"
        "  default: gemini\n"
        "  providers:\n"
        "    gemini:\n"
        "      type: api\n"
        "      model: gemini-test\n"
        "      api_key: env:GOOGLE_API_KEY\n"
        "agent:\n"
        f"  db_path: {tmp_path / 'conversation.db'}\n"
        "  goal_loop:\n"
        "    enabled: false\n"
        "memory:\n"
        "  rag:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr("simpleclaw.agent.orchestrator.create_router", MagicMock())
    orch = AgentOrchestrator(config)
    orch._router.send = AsyncMock()

    result = await orch.process_message("/goal 목표", user_id=1, chat_id=1)

    assert "비활성화" in result
    orch._router.send.assert_not_called()


@pytest.mark.asyncio
async def test_goal_rounds_disable_cron_mutation(config_file: Path):
    orch = AgentOrchestrator(config_file)
    seen = []

    async def fake_round(text, **kwargs):
        seen.append(kwargs)
        return ToolLoopResult(text="round answer", iterations=1)

    orch._run_tool_loop_result = fake_round
    orch._router.send = AsyncMock(
        return_value=LLMResponse(text='{"status":"done","reason":"ok","confidence":"high"}')
    )

    await orch.process_message("/goal 목표", user_id=1, chat_id=1)

    assert seen
    assert seen[0]["allow_cron_mutation"] is False
    assert seen[0]["on_text_delta"] is None
