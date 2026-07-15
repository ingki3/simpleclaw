"""오케스트레이터 drain 게이트 테스트 (BIZ-442).

검증 범위:
- drain 중 새 ``process_message`` 는 파이프라인 진입 없이 점검 응답을 반환
- drain 중 ``process_cron_message`` 는 실행을 건너뛰고 skip 사유를 반환
- drain 이 아니면 기존 파이프라인으로 위임되고 turn 이 operation 으로 추적됨
- drain 요청 이전에 시작된 turn 은 끊기지 않고 완료됨
- ``daemon.drain.state_file`` config 가 컨트롤러에 반영됨
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.daemon.drain import (
    DRAIN_CRON_SKIPPED_MESSAGE,
    DRAIN_MAINTENANCE_MESSAGE,
)


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

daemon:
  drain:
    state_file: "{tmp_path}/drain_state.json"
    default_timeout: 60
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


@pytest.fixture
def orchestrator(config_file):
    return AgentOrchestrator(config_file)


class TestDrainConfigWiring:
    def test_state_file_comes_from_config(self, orchestrator, config_file):
        expected = config_file.parent / "drain_state.json"
        assert orchestrator.drain_controller.state_file == expected


class TestProcessMessageDrainGate:
    @pytest.mark.asyncio
    async def test_draining_rejects_new_intake(self, orchestrator):
        orchestrator.drain_controller.request_drain("deploy", timeout=60)
        orchestrator._process_message_impl = AsyncMock(return_value="should-not-run")

        result = await orchestrator.process_message("안녕", 1, 100)

        assert result == DRAIN_MAINTENANCE_MESSAGE
        orchestrator._process_message_impl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_draining_delegates_to_pipeline(self, orchestrator):
        orchestrator._process_message_impl = AsyncMock(return_value="pipeline-answer")

        result = await orchestrator.process_message("안녕", 1, 100)

        assert result == "pipeline-answer"
        orchestrator._process_message_impl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_turn_is_tracked_as_active_operation(self, orchestrator):
        observed: list[int] = []

        async def impl(*args, **kwargs):
            observed.append(orchestrator.drain_controller.active_operations())
            return "ok"

        orchestrator._process_message_impl = impl

        await orchestrator.process_message("안녕", 1, 100)

        # 실행 중에는 1, 완료 후 0 — deploy script 의 quiesce 폴링 근거.
        assert observed == [1]
        assert orchestrator.drain_controller.active_operations() == 0

    @pytest.mark.asyncio
    async def test_turn_started_before_drain_completes(self, orchestrator):
        """게이트는 진입 시 1회만 평가 — 진행 중 turn 은 drain 이 걸려도 완료된다."""

        async def impl(*args, **kwargs):
            # turn 실행 도중 deploy script 가 drain 을 요청하는 상황.
            orchestrator.drain_controller.request_drain("deploy", timeout=60)
            return "completed"

        orchestrator._process_message_impl = impl

        result = await orchestrator.process_message("안녕", 1, 100)
        assert result == "completed"

        # 이후 새 intake 는 거절된다.
        followup = await orchestrator.process_message("또 안녕", 1, 100)
        assert followup == DRAIN_MAINTENANCE_MESSAGE

    @pytest.mark.asyncio
    async def test_operation_released_when_pipeline_raises(self, orchestrator):
        orchestrator._process_message_impl = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await orchestrator.process_message("안녕", 1, 100)

        assert orchestrator.drain_controller.active_operations() == 0


class TestProcessCronMessageDrainGate:
    @pytest.mark.asyncio
    async def test_draining_skips_cron_execution(self, orchestrator):
        orchestrator.drain_controller.request_drain("deploy", timeout=60)
        orchestrator._tool_loop = AsyncMock(return_value="should-not-run")

        result = await orchestrator.process_cron_message("cron prompt")

        assert result == DRAIN_CRON_SKIPPED_MESSAGE
        orchestrator._tool_loop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_draining_runs_cron(self, orchestrator):
        orchestrator._tool_loop = AsyncMock(return_value="cron-answer")

        result = await orchestrator.process_cron_message("cron prompt")

        assert result == "cron-answer"
        orchestrator._tool_loop.assert_awaited_once()
        assert orchestrator.drain_controller.active_operations() == 0
