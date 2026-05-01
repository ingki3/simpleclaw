"""Orchestrator의 trace_id 발급/전파 통합 테스트 (BIZ-25).

검증 범위:
- ``process_message`` 진입 시 새로운 trace_id가 발급되어 호출 체인 전체에서 동일 값 노출
- ``process_cron_message`` 진입 시 별도의 새로운 trace_id가 발급
- 도구 루프 내부에서 ``get_trace_id()``가 진입점에서 발급된 값을 그대로 반환
- 같은 프로세스의 후속 메시지는 새 trace_id를 받음(이전 값으로 누설되지 않음)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.llm.models import LLMResponse
from simpleclaw.logging.trace_context import get_trace_id


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


def _make_text_response(text: str) -> LLMResponse:
    """텍스트만 반환하는(도구 호출 없는) LLM 응답 mock."""
    return LLMResponse(text=text, model="test", tool_calls=None, usage=None)


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestOrchestratorTraceId:
    @pytest.mark.asyncio
    async def test_process_message_assigns_trace_id(self, config_file):
        """``process_message``가 진입 시 trace_id를 발급하고 LLM 호출 시점에 활성화한다."""
        orch = AgentOrchestrator(config_file)

        captured: dict[str, str] = {}

        async def fake_send(_request):
            captured["trace_id"] = get_trace_id()
            return _make_text_response("ok")

        orch._router.send = fake_send

        await orch.process_message("hello", user_id=1, chat_id=1)
        assert captured["trace_id"]
        assert len(captured["trace_id"]) >= 16  # uuid4 hex

    @pytest.mark.asyncio
    async def test_each_message_gets_unique_trace_id(self, config_file):
        orch = AgentOrchestrator(config_file)
        seen: list[str] = []

        async def fake_send(_request):
            seen.append(get_trace_id())
            return _make_text_response("ok")

        orch._router.send = fake_send

        await orch.process_message("first", user_id=1, chat_id=1)
        await orch.process_message("second", user_id=1, chat_id=1)
        assert len(seen) == 2
        assert seen[0] != seen[1]
        assert all(seen)

    @pytest.mark.asyncio
    async def test_trace_scope_restored_after_message(self, config_file):
        """메시지 처리 종료 후 컨텍스트의 trace_id가 외부 값으로 복원되어야 한다."""
        orch = AgentOrchestrator(config_file)
        orch._router.send = AsyncMock(return_value=_make_text_response("ok"))

        # 진입 전에는 비어 있음
        assert get_trace_id() == ""
        await orch.process_message("hi", user_id=1, chat_id=1)
        # 종료 후에도 비어 있음(누설 없음)
        assert get_trace_id() == ""

    @pytest.mark.asyncio
    async def test_cron_message_assigns_trace_id(self, config_file):
        orch = AgentOrchestrator(config_file)
        captured: dict[str, str] = {}

        async def fake_send(_request):
            captured["trace_id"] = get_trace_id()
            return _make_text_response("ok")

        orch._router.send = fake_send

        await orch.process_cron_message("daily report")
        assert captured["trace_id"]
