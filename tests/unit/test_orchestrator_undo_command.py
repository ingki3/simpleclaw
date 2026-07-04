"""Orchestrator /undo command tests (BIZ-366)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator


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
  history_limit: 5
  db_path: "{tmp_path}/conversations.db"

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


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestUndoCommand:
    @pytest.mark.asyncio
    async def test_undo_default_hides_last_turn_before_llm(self, config_file):
        orch = AgentOrchestrator(config_file)
        orch._tool_loop = AsyncMock(side_effect=["first answer", "second answer"])

        assert await orch.process_message("first", user_id=1, chat_id=1) == "first answer"
        result = await orch.process_message("/undo", user_id=1, chat_id=1)

        assert "최근 1턴" in result
        assert "대화 이력에 저장하지 않습니다" in result
        assert [msg.content for msg in orch._store.get_recent(limit=10)] == []
        assert [
            msg.content for msg in orch._store.get_recent(limit=10, include_deleted=True)
        ] == ["first", "first answer"]
        assert orch._tool_loop.await_count == 1

    @pytest.mark.asyncio
    async def test_undo_two_hides_two_recent_user_turns(self, config_file):
        orch = AgentOrchestrator(config_file)
        orch._tool_loop = AsyncMock(side_effect=["a1", "a2", "a3"])
        await orch.process_message("u1", user_id=1, chat_id=1)
        await orch.process_message("u2", user_id=1, chat_id=1)
        await orch.process_message("u3", user_id=1, chat_id=1)

        result = await orch.process_message("/undo 2", user_id=1, chat_id=1)

        assert "최근 2턴" in result
        assert [msg.content for msg in orch._store.get_recent(limit=10)] == ["u1", "a1"]

    @pytest.mark.asyncio
    async def test_undo_rejects_invalid_numbers_without_saving(self, config_file):
        orch = AgentOrchestrator(config_file)
        orch._tool_loop = AsyncMock()

        for text in ["/undo 0", "/undo -1", "/undo two", "/undo 1 extra"]:
            result = await orch.process_message(text, user_id=1, chat_id=1)
            assert result == "사용법: /undo 또는 /undo N (N은 1 이상의 정수)"

        assert orch._store.get_recent(limit=10) == []
        orch._tool_loop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_undo_reports_when_no_user_turn_exists(self, config_file):
        orch = AgentOrchestrator(config_file)
        orch._tool_loop = AsyncMock()

        result = await orch.process_message("/undo", user_id=1, chat_id=1)

        assert result == "되돌릴 대화 턴이 없습니다."
        assert orch._store.get_recent(limit=10) == []
        orch._tool_loop.assert_not_awaited()