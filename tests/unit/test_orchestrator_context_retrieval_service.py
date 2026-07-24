"""BIZ-345 context retrieval service delegation regression tests."""

import pytest

from simpleclaw.agent.context_retrieval import (
    ContextRetrievalConfig,
    ContextRetrievalService,
)
from simpleclaw.agent.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_orchestrator_delegates_context_retrieval_to_service(tmp_path):
    """오케스트레이터의 레거시 메서드는 별도 service 호출만 담당해야 한다."""
    _ = tmp_path
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    class StubContextRetrieval:
        def __init__(self):
            self.calls = []

        async def retrieve(self, user_text, exclude_contents=None):
            self.calls.append((user_text, exclude_contents))
            return "delegated context"

    stub = StubContextRetrieval()
    orch._context_retrieval = stub

    result = await orch._retrieve_relevant_context("질문", exclude_contents={"이미 본 내용"})

    assert result == "delegated context"
    assert stub.calls == [("질문", {"이미 본 내용"})]


def test_context_retrieval_service_keeps_config_values():
    """새 service는 retrieval 설정을 자체 config로 보관한다."""
    config = ContextRetrievalConfig(
        rag_top_k=7,
        rag_threshold=0.42,
        long_term_enabled=True,
        long_term_top_k=3,
        long_term_min_confidence=0.75,
        long_term_promotion_threshold=2,
        long_term_context_budget_chars=1234,
        long_term_per_item_chars=321,
        long_term_insights_file="/tmp/insights.jsonl",
        long_term_active_projects_file="/tmp/projects.jsonl",
        long_term_active_projects_window_days=5,
    )

    service = ContextRetrievalService(
        store=None,
        embedding_service=None,
        config=config,
        structured_logger=None,
    )

    assert service._rag_top_k == 7
    assert service._rag_threshold == 0.42
    assert service._long_term_active_projects_window_days == 5
