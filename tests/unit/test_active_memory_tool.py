"""BIZ-325 Active Memory 온디맨드 검색 도구 단위 테스트.

검증 범위:
- Native Function Calling 스키마에 search_memory가 노출된다.
- orchestrator dispatch가 tool 호출 시점의 query로 embedding을 만들고, conversation
  RAG + DB-backed memory_items를 tool result로 포맷한다.
- cluster_summary는 prompt contamination 방지를 위해 제외한다.
- 결과 없음/비활성 상태가 LLM loop 예외가 아닌 사용자 가시 문자열로 반환된다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.agent.tool_schemas import build_tool_definitions
from simpleclaw.llm.models import ToolCall
from simpleclaw.memory.models import ConversationMessage, MemoryItemType, MessageRole


def _write_config(tmp_path, *, rag_enabled: bool = True):
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
  max_tool_iterations: 4

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
    enabled: true
    top_k: 3
    min_confidence: 0.7
    promotion_threshold: 3
    context_budget_chars: 1200
    per_item_chars: 160
    insights_file: "{tmp_path}/insights.jsonl"
    active_projects_file: "{tmp_path}/active_projects.jsonl"
    active_projects_window_days: 14
""", encoding="utf-8")
    (tmp_path / "persona_local").mkdir(exist_ok=True)
    (tmp_path / "persona_local" / "AGENT.md").write_text("# Agent\nYou are SimpleClaw.", encoding="utf-8")
    (tmp_path / "local_skills").mkdir(exist_ok=True)
    (tmp_path / "global_skills").mkdir(exist_ok=True)
    return cfg


def _msg(content: str) -> ConversationMessage:
    return ConversationMessage(role=MessageRole.USER, content=content)


@patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
class TestActiveMemoryTool:
    def test_tool_schema_includes_search_memory(self):
        tools = {tool.name: tool for tool in build_tool_definitions([])}

        assert "search_memory" in tools
        schema = tools["search_memory"].parameters
        assert schema["required"] == ["query"]
        assert "top_k" in schema["properties"]
        assert schema["properties"]["top_k"]["minimum"] == 1
        assert schema["properties"]["top_k"]["maximum"] == 10

    @pytest.mark.asyncio
    async def test_dispatch_search_memory_returns_memory_items_and_conversation_hits(self, tmp_path):
        orch = AgentOrchestrator(_write_config(tmp_path))
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))
        mid = orch._store.add_message(_msg("BIZ-325 Active Memory는 온디맨드 검색 도구입니다."))
        orch._store.add_embedding(mid, [1.0, 0.0])
        orch._store.create_memory_item(
            item_type=MemoryItemType.MEMORY,
            text="사용자는 Active Memory를 PR merge 후 live deploy까지 원합니다.",
            source="manual",
            source_ref="memory:active-memory",
            confidence=0.95,
            importance=0.8,
            embedding=[1.0, 0.0],
        )

        result = await orch._dispatch_tool_call(ToolCall(
            id="call-1",
            name="search_memory",
            arguments={"query": "Active Memory 배포", "top_k": 2},
        ))

        orch._embedding_service.encode_query.assert_called_once_with("Active Memory 배포")
        assert "## Active Memory 검색 결과" in result
        assert "### 장기기억" in result
        assert "PR merge 후 live deploy" in result
        assert "### 관련 과거 대화" in result
        assert "온디맨드 검색 도구" in result
        assert "score=" in result

    @pytest.mark.asyncio
    async def test_cluster_summary_items_are_excluded(self, tmp_path):
        orch = AgentOrchestrator(_write_config(tmp_path))
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))
        orch._store.create_memory_item(
            item_type=MemoryItemType.CLUSTER_SUMMARY,
            text="cluster:69 link-to-wiki check_new_emails 오래된 자동화 히스토리",
            source="cluster",
            source_ref="cluster:69",
            confidence=0.99,
            importance=1.0,
            embedding=[1.0, 0.0],
        )
        orch._store.create_memory_item(
            item_type=MemoryItemType.MEMORY,
            text="허용되는 Active Memory 결과",
            source="manual",
            source_ref="memory:ok",
            confidence=0.95,
            importance=0.7,
            embedding=[1.0, 0.0],
        )

        result = await orch._dispatch_tool_call(ToolCall(
            id="call-2",
            name="search_memory",
            arguments={"query": "Active Memory 자동화", "top_k": 5},
        ))

        assert "허용되는 Active Memory 결과" in result
        assert "cluster:69" not in result
        assert "link-to-wiki" not in result
        assert "check_new_emails" not in result

    @pytest.mark.asyncio
    async def test_no_results_returns_explicit_message(self, tmp_path):
        orch = AgentOrchestrator(_write_config(tmp_path))
        orch._embedding_service.encode_query = MagicMock(return_value=np.array([1.0, 0.0], dtype=np.float32))

        result = await orch._dispatch_tool_call(ToolCall(
            id="call-3",
            name="search_memory",
            arguments={"query": "없는 기억"},
        ))

        assert result == "검색 결과가 없습니다."

    @pytest.mark.asyncio
    async def test_disabled_rag_returns_stable_message(self, tmp_path):
        orch = AgentOrchestrator(_write_config(tmp_path, rag_enabled=False))

        result = await orch._dispatch_tool_call(ToolCall(
            id="call-4",
            name="search_memory",
            arguments={"query": "Active Memory"},
        ))

        assert "비활성화" in result
        assert "memory.rag.enabled" in result
