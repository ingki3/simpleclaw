"""BIZ-79 E2E — Dreaming Dry-run + Admin Review Loop.

검증 범위 (DoD §B 의 핵심 사이클):

1. dreaming.run() 결과가 곧바로 USER.md 에 auto-promote 되지 않고
   ``insight_suggestions.jsonl`` 큐에 pending 상태로 적재된다 (단발 관측
   confidence 0.4 + evidence 1 → auto-promote 임계 미달).
2. 운영자가 reject 하면 (`BlocklistStore.add`) 다음 dreaming 사이클은
   같은 topic 을 큐에도 sidecar 에도 등록하지 않는다 (blocklist 사전 필터).
3. auto_promote 두 조건(confidence AND evidence_count)을 동시에 만족하는
   항목은 dry_run 중에도 큐를 우회해 USER.md 로 직접 승격된다 — "이미 충분히
   강한 시그널은 매번 승인할 필요가 없다" 는 운영자 부담 완화 정책.

이 모듈은 LLM 을 모킹해 정해진 user_insights_meta 를 반환시킴으로써
파이프라인 ↔ 큐 ↔ blocklist ↔ sidecar 사이의 4-자 상호작용만 검증한다.
LLM 품질/회귀는 ``test_dreaming.py``/``test_dreaming_regression.py`` 가 담당.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.insights import InsightStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.memory.suggestions import (
    BlocklistStore,
    SuggestionStore,
)


def _seed_files(tmp_path):
    """4종 marker 파일을 만들어 dreaming 의 fail-closed write 를 통과시킨다."""
    memory = tmp_path / "MEMORY.md"
    memory.write_text(
        "# Core Memory\n\nExisting.\n\n"
        "<!-- managed:dreaming:journal -->\n"
        "<!-- /managed:dreaming:journal -->\n"
    )
    user = tmp_path / "USER.md"
    user.write_text(
        "# User\n\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n"
    )
    return memory, user


def _mock_router(meta_topic: str, meta_text: str):
    """user_insights_meta 1건만 반환하는 LLM 라우터 모킹 헬퍼."""
    response = MagicMock()
    response.text = (
        '{"memory": "## d\\n- x", "user_insights": "- ' + meta_text + '", '
        '"user_insights_meta": [{"topic": "' + meta_topic + '", '
        '"text": "' + meta_text + '"}], '
        '"soul_updates": "", "agent_updates": ""}'
    )
    router = MagicMock()
    router.send = AsyncMock(return_value=response)
    return router


@pytest.fixture
def dryrun_pipeline(tmp_path):
    """dry_run=True 기본 + 큐/blocklist 가 활성화된 파이프라인."""
    memory, user = _seed_files(tmp_path)
    store = ConversationStore(tmp_path / "conv.db")
    suggestions_path = tmp_path / "insight_suggestions.jsonl"
    blocklist_path = tmp_path / "insight_blocklist.jsonl"
    insights_path = tmp_path / "insights.jsonl"

    pipeline = DreamingPipeline(
        conversation_store=store,
        memory_file=memory,
        user_file=user,
        insights_file=insights_path,
        suggestions_file=suggestions_path,
        blocklist_file=blocklist_path,
        # 자동 승격 임계는 평소 운영값으로 — 단발 관측은 자연히 큐 경유.
        auto_promote_confidence=0.7,
        auto_promote_evidence_count=3,
        insight_promotion_threshold=3,
    )
    return {
        "pipeline": pipeline,
        "store": store,
        "suggestions": SuggestionStore(suggestions_path),
        "blocklist": BlocklistStore(blocklist_path),
        "insights": InsightStore(insights_path),
    }


class TestDryRunQueueing:
    """1회 관측은 confidence 0.4 + evidence 1 — 어떤 auto-promote 임계도 못 넘는다."""

    @pytest.mark.asyncio
    async def test_single_observation_goes_to_queue_not_user_md(
        self, dryrun_pipeline, tmp_path
    ):
        ctx = dryrun_pipeline
        ctx["pipeline"]._router = _mock_router("정치뉴스", "정치 뉴스 관심")
        ctx["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="뉴스 보여줘")
        )
        await ctx["pipeline"].run()

        # USER.md insights 섹션은 비어있어야 — auto-promote 임계 미달이므로
        # 운영자 검수 대기 (sidecar 는 evidence_count 추적용으로 채워짐).
        user_text = (tmp_path / "USER.md").read_text()
        section = user_text.split("<!-- managed:dreaming:insights -->")[1]
        section = section.split("<!-- /managed:dreaming:insights -->")[0]
        assert section.strip() == ""

        # sidecar 는 evidence 추적을 위해 관측을 누적하되 confidence 는 단발 cap.
        sidecar = ctx["insights"].load()
        assert "정치뉴스" in sidecar
        assert sidecar["정치뉴스"].confidence < 0.7

        # 큐에는 pending 상태로 1건 적재.
        pending = ctx["suggestions"].list_pending()
        assert len(pending) == 1
        assert pending[0].topic == "정치뉴스"
        assert pending[0].status == "pending"


class TestRejectBlockingNextCycle:
    """DoD §B 의 핵심 가드: reject → next cycle blocks same insight."""

    @pytest.mark.asyncio
    async def test_rejected_topic_not_re_queued_on_next_cycle(self, dryrun_pipeline):
        ctx = dryrun_pipeline
        ctx["pipeline"]._router = _mock_router("정치뉴스", "정치 뉴스 관심")

        # 회차 1 — 큐 적재.
        ctx["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="뉴스 보여줘")
        )
        await ctx["pipeline"].run()
        assert len(ctx["suggestions"].list_pending()) == 1

        # 운영자 reject — blocklist 등재 + status 변경. (admin API 와 동일 인터페이스)
        ctx["blocklist"].add("정치뉴스", reason="user_rejected")
        target = ctx["suggestions"].find_pending_by_topic("정치뉴스")
        assert target is not None
        ctx["suggestions"].update_status(target.id, "rejected")

        # 회차 2 — 같은 topic 이 다시 관측되어도, 큐 default(pending) 에 다시 뜨면 안 됨.
        ctx["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="정치 또")
        )
        await ctx["pipeline"].run()

        # default 큐 노출에서 같은 topic 이 부활하면 안 된다.
        pending_topics = [item.topic for item in ctx["suggestions"].list_pending()]
        assert "정치뉴스" not in pending_topics
        # sidecar 의 evidence_count 도 회차 1 그대로 — blocklist 가 회차 2 관측을
        # 사전 차단해 reinforcement 가 발생하지 않는다.
        sidecar = ctx["insights"].load()
        assert sidecar["정치뉴스"].evidence_count == 1

    @pytest.mark.asyncio
    async def test_rejected_topic_normalized_form_also_blocked(self, dryrun_pipeline):
        """공백/대소문자 다른 표기로 다시 들어와도 blocklist 가 정규형 일치로 차단."""
        ctx = dryrun_pipeline
        ctx["pipeline"]._router = _mock_router("정치 뉴스", "정치 뉴스 관심")

        # 운영자가 정규형 키로 reject.
        ctx["blocklist"].add("정치뉴스", reason="user_rejected")

        ctx["store"].add_message(
            ConversationMessage(role=MessageRole.USER, content="정치")
        )
        await ctx["pipeline"].run()

        # "정치 뉴스" 와 "정치뉴스" 는 같은 정규형 → 큐에도 sidecar 에도 들어가지 않음.
        assert ctx["suggestions"].list_pending() == []
        assert ctx["insights"].load() == {}
