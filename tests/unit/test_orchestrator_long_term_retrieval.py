"""BIZ-307 Phase 1 장기기억 Retrieval 통합 테스트.

운영자 정정 범위: `_retrieve_relevant_context()`가 기존 conversation RAG와 함께
Dreaming sidecar(InsightStore/active-projects) 및 cluster summary를 회상해야 한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.logging.structured_logger import StructuredLogger
from simpleclaw.memory.active_projects import ActiveProject, ActiveProjectStore
from simpleclaw.memory.insights import InsightMeta, InsightStore


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


@pytest.fixture
def long_term_config(tmp_path):
    def _make(*, rag_enabled=True, long_term_enabled=True):
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
    enabled: {str(rag_enabled).lower()}
    model: "test-model"
    top_k: 3
    similarity_threshold: 0.5
  long_term:
    enabled: {str(long_term_enabled).lower()}
    top_k: 2
    min_confidence: 0.7
    promotion_threshold: 3
    context_budget_chars: 900
    per_item_chars: 180
    insights_file: "{tmp_path}/insights.jsonl"
    active_projects_file: "{tmp_path}/active_projects.jsonl"
    active_projects_window_days: 14
""")
        (tmp_path / "persona_local").mkdir(exist_ok=True)
        (tmp_path / "persona_local" / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.")
        (tmp_path / "local_skills").mkdir(exist_ok=True)
        (tmp_path / "global_skills").mkdir(exist_ok=True)
        return cfg
    return _make


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestLongTermRetrieval:
    @pytest.mark.asyncio
    async def test_long_term_gate_keeps_conversation_rag_only_when_disabled(self, long_term_config):
        cfg = long_term_config(long_term_enabled=False)
        orch = AgentOrchestrator(cfg)
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))
        mid = orch._store.add_message(_msg("맥북 가격은 240만원"))
        orch._store.add_embedding(mid, [1.0, 0.0])

        block = await orch._retrieve_relevant_context("맥북 가격")

        assert "관련 과거 대화" in block
        assert "240만원" in block
        assert "장기기억" not in block

    @pytest.mark.asyncio
    async def test_active_promoted_insights_are_injected_in_separate_section(self, long_term_config, tmp_path):
        cfg = long_term_config()
        InsightStore(tmp_path / "insights.jsonl").save_all({
            "simpleclaw": InsightMeta(
                topic="SimpleClaw 장기기억",
                text="사용자는 SimpleClaw에서 장기기억 Retrieval 통합을 진행 중입니다.",
                confidence=0.82,
                evidence_count=3,
            )
        })
        orch = AgentOrchestrator(cfg)
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))

        block = await orch._retrieve_relevant_context("SimpleClaw 장기기억 진행 상황")

        assert "## 장기기억" in block
        assert "SimpleClaw 장기기억" in block
        assert "confidence=0.82" in block
        assert "evidence=3" in block
        assert "장기기억 Retrieval 통합" in block
        assert "## 관련 과거 대화" not in block

    @pytest.mark.asyncio
    async def test_archived_low_confidence_and_unpromoted_insights_are_excluded(self, long_term_config, tmp_path):
        cfg = long_term_config()
        now = datetime.now()
        InsightStore(tmp_path / "insights.jsonl").save_all({
            "archived": InsightMeta(topic="archive", text="아카이브 항목", confidence=0.9, evidence_count=5, archived_at=now),
            "low": InsightMeta(topic="low", text="낮은 confidence", confidence=0.5, evidence_count=5),
            "single": InsightMeta(topic="single", text="단발 미승격", confidence=0.9, evidence_count=1),
            "active": InsightMeta(topic="active", text="활성 승격 항목", confidence=0.9, evidence_count=3),
        })
        orch = AgentOrchestrator(cfg)
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))

        block = await orch._retrieve_relevant_context("active archive low single")

        assert "활성 승격 항목" in block
        assert "아카이브 항목" not in block
        assert "낮은 confidence" not in block
        assert "단발 미승격" not in block

    @pytest.mark.asyncio
    async def test_active_projects_and_cluster_summaries_join_long_term_context(self, long_term_config, tmp_path):
        cfg = long_term_config()
        ActiveProjectStore(tmp_path / "active_projects.jsonl").save_all({
            "multica": ActiveProject(
                name="Multica",
                role="운영자",
                recent_summary="이슈 기반 코딩 에이전트 워크플로를 정리 중입니다.",
                last_seen=datetime.now() - timedelta(days=1),
            )
        })
        orch = AgentOrchestrator(cfg)
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))
        orch._store.create_cluster(
            "장기기억 클러스터",
            [1.0, 0.0],
            summary="Dreaming이 장기기억 검색 파이프라인 논의를 요약했습니다.",
            member_count=4,
        )

        block = await orch._retrieve_relevant_context("Multica 장기기억 검색")

        assert "## 장기기억" in block
        assert "active_project" in block
        assert "Multica" in block
        assert "## 클러스터 요약" in block
        assert "장기기억 검색 파이프라인" in block

    @pytest.mark.asyncio
    async def test_sidecar_failures_fallback_to_conversation_rag_and_log_source_errors(self, long_term_config, tmp_path):
        cfg = long_term_config()
        (tmp_path / "insights.jsonl").write_text("{broken-json\n", encoding="utf-8")
        sl = StructuredLogger(log_dir=tmp_path / "logs")
        orch = AgentOrchestrator(cfg, structured_logger=sl)
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))
        mid = orch._store.add_message(_msg("conversation fallback survives"))
        orch._store.add_embedding(mid, [1.0, 0.0])
        orch._store.list_clusters = MagicMock(side_effect=RuntimeError("cluster db down"))

        block = await orch._retrieve_relevant_context("fallback")

        assert "conversation fallback survives" in block
        [entry] = sl.get_entries()
        assert entry.action_type == "rag_retrieve"
        assert entry.status == "partial"
        assert entry.details["conversation"]["count"] == 1
        assert entry.details["long_term"]["errors"] >= 1
        assert entry.details["cluster_summary"]["errors"] == 1
        assert "context_chars" in entry.details

    @pytest.mark.asyncio
    async def test_top_k_budget_and_dedupe_limit_long_term_prompt_bloat(self, long_term_config, tmp_path):
        cfg = long_term_config()
        long_text = "SimpleClaw 중복 메모리 " + ("길다 " * 200)
        InsightStore(tmp_path / "insights.jsonl").save_all({
            "a": InsightMeta(topic="SimpleClaw A", text=long_text, confidence=0.95, evidence_count=5),
            "b": InsightMeta(topic="SimpleClaw B", text="SimpleClaw 두 번째 핵심 기억", confidence=0.9, evidence_count=4),
            "c": InsightMeta(topic="SimpleClaw C", text="SimpleClaw 세 번째는 top_k 때문에 제외", confidence=0.88, evidence_count=4),
        })
        orch = AgentOrchestrator(cfg)
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))

        block = await orch._retrieve_relevant_context("SimpleClaw", exclude_contents={long_text})

        assert "SimpleClaw 중복 메모리" not in block
        assert "SimpleClaw 두 번째 핵심 기억" in block
        assert "SimpleClaw 세 번째는 top_k 때문에 제외" not in block
        assert len(block) <= 900


def _msg(content: str):
    from simpleclaw.memory.models import ConversationMessage, MessageRole

    return ConversationMessage(role=MessageRole.USER, content=content)
