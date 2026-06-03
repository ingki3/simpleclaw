"""Dreaming 기반 proactive 후보 추출기 단위 테스트."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.models import (
    CHANNEL_RECIPE_PREFIX,
    ConversationMessage,
    MessageRole,
)
from simpleclaw.proactive.dreaming_extractor import DreamingOpportunityExtractor
from simpleclaw.proactive.models import OpportunityType
from simpleclaw.proactive.store import OpportunityStore


BASE = datetime(2026, 6, 1, 8, 30)


def _msg(
    content: str,
    *,
    days_ago: int,
    hour: int = 8,
    minute: int = 30,
    channel: str | None = "telegram",
) -> tuple[int, ConversationMessage]:
    """테스트 입력용 rowid/message 쌍을 만든다."""
    return (
        100 + days_ago,
        ConversationMessage(
            role=MessageRole.USER,
            content=content,
            timestamp=BASE
            - timedelta(days=days_ago)
            + timedelta(
                hours=hour - BASE.hour,
                minutes=minute - BASE.minute,
            ),
            channel=channel,
        ),
    )


def test_repeated_market_summary_requests_create_cron_opportunity() -> None:
    """최근 14일 안의 유사한 오전 반복 요청은 cron 제안 후보로 승격된다."""
    pairs = [
        _msg("오늘 아침 시장 요약해줘", days_ago=day, hour=8 + (day % 2))
        for day in [0, 2, 5, 8, 11]
    ]
    extractor = DreamingOpportunityExtractor(now=BASE, min_occurrences=5)

    opportunities = extractor.extract(pairs)

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.type == OpportunityType.REPEATED_REQUEST
    assert opportunity.cooldown_key == "dreaming:repeated:market_summary"
    assert opportunity.suggested_action.kind == "create_cron"
    assert opportunity.confidence >= 0.8
    assert set(opportunity.source_msg_ids) == {100, 102, 105, 108, 111}
    assert any("count=5" in item for item in opportunity.evidence)
    assert any("hour_bucket=08:00-10:00" in item for item in opportunity.evidence)


def test_pending_upsert_refreshes_same_cooldown_key(tmp_path) -> None:
    """같은 topic/cooldown_key 후보는 pending row를 중복 생성하지 않고 갱신한다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    first_pairs = [
        _msg("오늘 아침 시장 요약해줘", days_ago=day, hour=8) for day in [0, 1, 2, 3, 4]
    ]
    second_pairs = [
        _msg("오늘 오전 시장 브리핑해줘", days_ago=day, hour=9)
        for day in [0, 1, 2, 3, 4, 5]
    ]
    extractor = DreamingOpportunityExtractor(now=BASE, min_occurrences=5)

    first = store.upsert_pending_by_cooldown_key(extractor.extract(first_pairs)[0])
    updated = store.upsert_pending_by_cooldown_key(extractor.extract(second_pairs)[0])

    rows = store.list_all()
    assert len(rows) == 1
    assert updated.id == first.id
    assert rows[0].confidence >= first.confidence
    assert any("count=6" in item for item in rows[0].evidence)


def test_auto_trigger_only_corpus_does_not_create_repeated_interest() -> None:
    """recipe/cron 같은 자동 트리거 출처만 있는 코퍼스는 반복 관심으로 오탐하지 않는다."""
    pairs = [
        _msg(
            "오늘 아침 시장 요약해줘",
            days_ago=day,
            hour=8,
            channel=f"{CHANNEL_RECIPE_PREFIX}market-summary",
        )
        for day in [0, 1, 2, 3, 4, 5]
    ]
    extractor = DreamingOpportunityExtractor(now=BASE, min_occurrences=5)

    assert extractor.extract(pairs) == []


def test_interest_based_candidates_are_disabled_by_default() -> None:
    """관심 정보 후보는 MVP 기본값에서 비활성화되어 반복 요청만 반환한다."""
    pairs = [
        _msg("요즘 양자컴퓨팅 소식 궁금해", days_ago=day, hour=21) for day in [0, 3, 6]
    ]
    extractor = DreamingOpportunityExtractor(now=BASE, min_occurrences=5)

    assert extractor.extract(pairs) == []


class RaisingExtractor:
    """DreamingPipeline hook 실패가 전체 사이클로 전파되지 않는지 검증용 fake."""

    def extract(self, id_pairs):
        raise RuntimeError("extractor boom")


def _managed_file(path, section):
    path.write_text(
        f"# {path.stem}\n\n<!-- managed:dreaming:{section} -->\n<!-- /managed:dreaming:{section} -->\n",
        encoding="utf-8",
    )


def test_extractor_exception_does_not_break_dreaming_pipeline(tmp_path) -> None:
    """proactive hook 예외는 logging/skip으로 흡수되고 기존 dreaming write는 유지된다."""
    store = ConversationStore(tmp_path / "conversation.db")
    store.add_message(
        ConversationMessage(role=MessageRole.USER, content="오늘 있었던 일 기억해줘")
    )
    memory_file = tmp_path / "MEMORY.md"
    user_file = tmp_path / "USER.md"
    _managed_file(memory_file, "journal")
    _managed_file(user_file, "insights")
    pipeline = DreamingPipeline(
        store,
        memory_file,
        user_file=user_file,
        proactive_extractor=RaisingExtractor(),
        opportunity_store=OpportunityStore(tmp_path / "opportunities.jsonl"),
    )

    entry = asyncio.run(pipeline.run())

    assert entry is not None
    assert "기억" in entry.summary
    assert not (tmp_path / "opportunities.jsonl").exists()


def test_pipeline_stores_extracted_opportunities_without_sending(tmp_path) -> None:
    """Dreaming run은 후보를 pending queue에 저장만 하고 직접 발송하지 않는다."""
    store = ConversationStore(tmp_path / "conversation.db")
    for day in [0, 1, 2, 3, 4]:
        store.add_message(_msg("오늘 아침 시장 요약해줘", days_ago=day, hour=8)[1])
    memory_file = tmp_path / "MEMORY.md"
    user_file = tmp_path / "USER.md"
    _managed_file(memory_file, "journal")
    _managed_file(user_file, "insights")
    opportunity_store = OpportunityStore(tmp_path / "opportunities.jsonl")
    pipeline = DreamingPipeline(
        store,
        memory_file,
        user_file=user_file,
        proactive_extractor=DreamingOpportunityExtractor(now=BASE, min_occurrences=5),
        opportunity_store=opportunity_store,
    )

    asyncio.run(pipeline.run())

    rows = opportunity_store.list_all()
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].presented_count == 0
    assert rows[0].last_presented_at is None
    assert rows[0].message_draft
    assert rows[0].suggested_action.payload["timezone"] == "local"
