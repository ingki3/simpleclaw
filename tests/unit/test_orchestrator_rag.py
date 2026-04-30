"""Orchestrator의 RAG 회상 통합 테스트 (spec 005 Phase 2).

검증 범위:
- RAG 비활성 시 기존 동작(슬라이딩 윈도우만)이 그대로 유지되는지
- RAG 활성 시 _retrieve_relevant_context가 시스템 프롬프트에 회상 블록을 주입하는지
- 임계값 미만 결과는 필터링되는지
- 최근 윈도우 중복 메시지는 제외되는지
- 임베딩 서비스 실패 시 빈 문자열을 반환하여 fallback 하는지
- _save_turn이 user/assistant 메시지를 저장하고 백그라운드 임베딩을 스케줄하는지
- isolated 모드(cron)는 RAG를 호출하지 않는지
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.memory.models import ConversationMessage, MessageRole


@pytest.fixture
def config_file_rag_off(tmp_path):
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


@pytest.fixture
def config_file_rag_on(tmp_path):
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
  history_limit: 3
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
    enabled: true
    model: "test-model"
    top_k: 3
    similarity_threshold: 0.5
""")
    persona_dir = tmp_path / "persona_local"
    persona_dir.mkdir()
    (persona_dir / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    return cfg


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestRAGDisabled:
    def test_no_embedding_service_when_disabled(self, config_file_rag_off):
        orch = AgentOrchestrator(config_file_rag_off)
        assert orch._rag_enabled is False
        assert orch._embedding_service is None

    @pytest.mark.asyncio
    async def test_retrieve_returns_empty_when_disabled(self, config_file_rag_off):
        orch = AgentOrchestrator(config_file_rag_off)
        result = await orch._retrieve_relevant_context("아무 질문")
        assert result == ""

    @pytest.mark.asyncio
    async def test_save_turn_skips_embedding(self, config_file_rag_off):
        orch = AgentOrchestrator(config_file_rag_off)
        orch._save_turn("hello", "world")
        # 임베딩 서비스가 None이므로 백그라운드 태스크가 생성되지 않음
        assert len(orch._background_tasks) == 0
        assert orch._store.count() == 2


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestRAGEnabled:
    def test_embedding_service_created(self, config_file_rag_on):
        orch = AgentOrchestrator(config_file_rag_on)
        assert orch._rag_enabled is True
        assert orch._embedding_service is not None
        assert orch._embedding_service.model_name == "test-model"
        assert orch._rag_top_k == 3
        assert orch._rag_threshold == 0.5

    @pytest.mark.asyncio
    async def test_retrieve_returns_empty_when_no_query_vec(
        self, config_file_rag_on,
    ):
        """임베딩 서비스가 None을 반환하면 회상 블록도 빈 문자열."""
        orch = AgentOrchestrator(config_file_rag_on)
        orch._embedding_service.encode_query = MagicMock(return_value=None)
        result = await orch._retrieve_relevant_context("질문")
        assert result == ""

    @pytest.mark.asyncio
    async def test_retrieve_formats_block(self, config_file_rag_on):
        orch = AgentOrchestrator(config_file_rag_on)

        # 과거 메시지 시드 + 임베딩 부착
        old_ts = datetime(2026, 4, 10, 14, 30)
        mid = orch._store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT,
            content="맥북 프로 14인치 M3는 240만원입니다",
            timestamp=old_ts,
        ))
        orch._store.add_embedding(mid, [1.0, 0.0])

        # encode_query를 mock — 과거 임베딩과 동일 방향(완전 일치)
        orch._embedding_service.encode_query = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )

        block = await orch._retrieve_relevant_context("맥북 가격 얼마였지?")
        assert "관련 과거 대화" in block
        assert "240만원" in block
        assert "2026-04-10 14:30" in block
        assert "**assistant**" in block

    @pytest.mark.asyncio
    async def test_threshold_filters_low_similarity(self, config_file_rag_on):
        orch = AgentOrchestrator(config_file_rag_on)

        mid = orch._store.add_message(_msg("관련 없는 잡담"))
        # 직교 벡터 — 코사인 유사도 0 < threshold 0.5
        orch._store.add_embedding(mid, [0.0, 1.0])
        orch._embedding_service.encode_query = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )

        block = await orch._retrieve_relevant_context("질문")
        assert block == ""  # 임계값 미만으로 필터링

    @pytest.mark.asyncio
    async def test_excludes_recent_window_messages(self, config_file_rag_on):
        """최근 윈도우에 이미 포함된 메시지는 회상 블록에서 제외."""
        orch = AgentOrchestrator(config_file_rag_on)

        mid = orch._store.add_message(_msg("최근에 한 말"))
        orch._store.add_embedding(mid, [1.0, 0.0])
        orch._embedding_service.encode_query = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )

        # exclude_contents에 동일 본문 전달
        block = await orch._retrieve_relevant_context(
            "질문", exclude_contents={"최근에 한 말"},
        )
        assert block == ""

    @pytest.mark.asyncio
    async def test_search_failure_returns_empty(self, config_file_rag_on):
        """search_similar 예외 시 빈 문자열 반환(서비스 가용성)."""
        orch = AgentOrchestrator(config_file_rag_on)
        orch._embedding_service.encode_query = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        orch._store.search_similar = MagicMock(
            side_effect=RuntimeError("db corrupt")
        )
        result = await orch._retrieve_relevant_context("질문")
        assert result == ""


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestSaveTurnEmbedding:
    @pytest.mark.asyncio
    async def test_schedules_background_embedding(self, config_file_rag_on):
        """_save_turn이 user/assistant 양쪽에 대해 백그라운드 임베딩을 스케줄한다."""
        orch = AgentOrchestrator(config_file_rag_on)
        orch._embedding_service.encode_passage = MagicMock(
            return_value=np.array([0.1, 0.2], dtype=np.float32)
        )

        orch._save_turn("user msg", "assistant reply")

        # 두 백그라운드 태스크가 등록되어야 함
        assert len(orch._background_tasks) == 2
        # 메시지는 즉시 저장됨(임베딩 완료 전이라도)
        assert orch._store.count() == 2

        # 태스크 완료 대기
        import asyncio
        await asyncio.gather(*list(orch._background_tasks), return_exceptions=True)

        # encode_passage가 두 번 호출되어야 함(user, assistant)
        assert orch._embedding_service.encode_passage.call_count == 2


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestToolLoopIntegration:
    @pytest.mark.asyncio
    async def test_isolated_mode_skips_rag(self, config_file_rag_on):
        """isolated=True(cron)에서는 RAG가 호출되지 않아야 한다."""
        orch = AgentOrchestrator(config_file_rag_on)
        orch._embedding_service.encode_query = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )

        mock_response = MagicMock()
        mock_response.text = "ok"
        mock_response.tool_calls = None
        mock_response.backend_name = "gemini"
        orch._router = MagicMock()
        orch._router.send = AsyncMock(return_value=mock_response)

        await orch.process_cron_message("크론 메시지")
        assert orch._embedding_service.encode_query.call_count == 0

    @pytest.mark.asyncio
    async def test_non_isolated_calls_rag(self, config_file_rag_on):
        """일반 process_message는 _retrieve_relevant_context를 호출한다."""
        orch = AgentOrchestrator(config_file_rag_on)
        orch._embedding_service.encode_query = MagicMock(return_value=None)
        orch._embedding_service.encode_passage = MagicMock(return_value=None)

        mock_response = MagicMock()
        mock_response.text = "응답"
        mock_response.tool_calls = None
        mock_response.backend_name = "gemini"
        orch._router = MagicMock()
        orch._router.send = AsyncMock(return_value=mock_response)

        await orch.process_message("질문", 1, 1)
        # encode_query는 _retrieve_relevant_context에서 1회 호출
        assert orch._embedding_service.encode_query.call_count == 1


def _msg(content: str, role: MessageRole = MessageRole.USER, ts: datetime | None = None):
    return ConversationMessage(
        role=role,
        content=content,
        timestamp=ts or datetime.now(),
    )
