"""BIZ-316 — AGENT.md dreaming 갱신은 지속 정책만 보존한다."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.memory.agent_update_filter import filter_agent_updates
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import ConversationMessage, MessageRole


class _Response:
    """Dreaming LLM 테스트용 최소 응답 객체."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = None


class _Router:
    """파일별 dreaming 호출 순서를 고정해 응답하는 router."""

    def __init__(self, responses: list[str]) -> None:
        self.send = AsyncMock(side_effect=[_Response(text) for text in responses])


def test_filter_agent_updates_drops_cron_recipe_event_logs() -> None:
    """레시피/크론 생성·변경 완료 기록은 AGENT.md 정책이 아니다."""
    updates = "\n".join(
        [
            "- usstock-night 크론 시간을 23:30으로 변경함.",
            "- check_new_emails 레시피를 생성함.",
            "- link-to-wiki 레시피 설정을 수정했다.",
        ]
    )

    result = filter_agent_updates(updates)

    assert result.text == ""
    assert result.kept == []
    assert len(result.dropped) == 3


def test_filter_agent_updates_keeps_durable_policy_rules() -> None:
    """앞으로의 행동 규칙/운영 정책은 AGENT.md 갱신으로 유지한다."""
    updates = "- 앞으로 주식 레시피를 만들 때는 시장 상태/API/뉴스 원문을 교차검증한다."

    result = filter_agent_updates(updates)

    assert result.text == updates
    assert result.kept == [updates]
    assert result.dropped == []


def test_filter_agent_updates_drops_memory_summary_duplicates() -> None:
    """MEMORY summary와 의미가 겹치는 bullet은 AGENT.md에서 제거한다."""
    updates = "- 운영자가 usstock-night 크론 시간을 23:30으로 조정했다."
    memory_summary = "- usstock-night 크론 시간을 23:30으로 조정했다."

    result = filter_agent_updates(updates, memory_summary=memory_summary)

    assert result.text == ""
    assert result.dropped == [updates]


def test_filter_agent_updates_keeps_only_policy_from_mixed_bullets() -> None:
    """혼합 입력에서는 정책 bullet만 남기고 사건 기록은 제거한다."""
    updates = "\n".join(
        [
            "- usstock-night 크론 시간을 23:30으로 변경함.",
            "- 앞으로 주식 레시피를 만들 때는 시장 상태/API/뉴스 원문을 교차검증한다.",
            "- check_new_emails 레시피를 생성함.",
        ]
    )

    result = filter_agent_updates(updates)

    assert result.text == "- 앞으로 주식 레시피를 만들 때는 시장 상태/API/뉴스 원문을 교차검증한다."
    assert len(result.kept) == 1
    assert len(result.dropped) == 2


@pytest.mark.asyncio
async def test_summarize_filters_agent_updates_against_memory_summary(tmp_path) -> None:
    """DreamingPipeline은 저장 전 AGENT/MEMORY 중복 제거를 적용한다."""
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# Memory\n\n<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n",
        encoding="utf-8",
    )
    agent_file = tmp_path / "AGENT.md"
    agent_file.write_text(
        "# Agent\n\n<!-- managed:dreaming:dreaming-updates -->\n<!-- /managed:dreaming:dreaming-updates -->\n",
        encoding="utf-8",
    )
    router = _Router(
        [
            '{"memory":"- usstock-night 크론 시간을 23:30으로 변경했다."}',
            '{"user_insights":"","user_insights_meta":[]}',
            '{"soul_updates":""}',
            '{"agent_updates":"- usstock-night 크론 시간을 23:30으로 변경했다.\\n- 앞으로 주식 레시피를 만들 때는 시장 상태/API/뉴스 원문을 교차검증한다."}',
            '{"active_projects":[]}',
        ]
    )
    pipeline = DreamingPipeline(
        ConversationStore(tmp_path / "db.sqlite"),
        memory_file,
        agent_file=agent_file,
        llm_router=router,
    )

    result = await pipeline.summarize(
        [ConversationMessage(role=MessageRole.USER, content="주식 레시피 정책 업데이트")]
    )

    assert result["agent_updates"] == "- 앞으로 주식 레시피를 만들 때는 시장 상태/API/뉴스 원문을 교차검증한다."
    assert "23:30" not in result["agent_updates"]
