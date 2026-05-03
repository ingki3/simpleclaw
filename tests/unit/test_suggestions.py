"""Unit tests for BIZ-79 — Dreaming Dry-run + Admin Review Loop.

DoD 회귀 가드:

1. Dreaming 출력은 기본적으로 pending suggestion 으로 큐잉된다 (USER.md 즉시 쓰기 X).
2. Auto-promotion 은 confidence ≥ X **AND** evidence_count ≥ Y 를 동시에 만족할 때만.
3. Reject → blocklist 추가 → 다음 사이클에 같은 topic 재추출 차단.
4. Accept / edit 은 USER.md 에 bullet 으로 append, 큐 status 가 terminal 로 전환.
5. SuggestionStore 는 토픽당 1행 pending 보장 (반복 강화는 in-place 갱신).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.insights import (
    InsightMeta,
    InsightStore,
    normalize_topic,
)
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.memory.suggestions import (
    BlocklistStore,
    SuggestionMeta,
    SuggestionStore,
)


# ----------------------------------------------------------------------
# SuggestionStore — 큐 라이프사이클
# ----------------------------------------------------------------------


class TestSuggestionStore:
    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        store = SuggestionStore(tmp_path / "missing.jsonl")
        assert store.load() == []

    def test_upsert_pending_creates_row(self, tmp_path: Path):
        store = SuggestionStore(tmp_path / "s.jsonl")
        meta = InsightMeta(
            topic="맥북에어가격",
            text="맥북에어 가격을 조회함",
            evidence_count=1,
            confidence=0.4,
            source_msg_ids=[42],
        )
        meta.recompute_id_range()
        s = store.upsert_pending(meta)
        assert s.status == "pending"
        assert s.id  # uuid generated
        assert s.topic == "맥북에어가격"
        assert s.evidence_count == 1
        assert s.start_msg_id == 42
        assert s.end_msg_id == 42

        # roundtrip
        again = store.get(s.id)
        assert again is not None
        assert again.text == "맥북에어 가격을 조회함"

    def test_upsert_pending_is_idempotent_per_topic(self, tmp_path: Path):
        """동일 토픽으로 두 번 upsert 하면 in-place 갱신 — 한 토픽에 pending 1행."""
        store = SuggestionStore(tmp_path / "s.jsonl")
        m1 = InsightMeta(
            topic="정치뉴스",
            text="정치 뉴스 1회",
            evidence_count=1,
            confidence=0.4,
            source_msg_ids=[10],
        )
        m1.recompute_id_range()
        s1 = store.upsert_pending(m1)

        # 강화된 관측 (변형 표기) 으로 upsert.
        m2 = InsightMeta(
            topic="정치 뉴스!",
            text="정치 뉴스 2회 (갱신)",
            evidence_count=2,
            confidence=0.55,
            source_msg_ids=[10, 20],
        )
        m2.recompute_id_range()
        s2 = store.upsert_pending(m2)

        # 같은 행이 갱신되어야 한다 — id 보존.
        assert s2.id == s1.id
        assert s2.evidence_count == 2
        assert s2.confidence == pytest.approx(0.55)
        assert s2.text == "정치 뉴스 2회 (갱신)"
        # 큐 전체에는 여전히 1행만.
        assert len([s for s in store.load() if s.status == "pending"]) == 1

    def test_terminal_status_kept_separate_from_new_pending(self, tmp_path: Path):
        """rejected/accepted 행은 audit 보존 — 다른 row 로 새 pending 이 생긴다."""
        store = SuggestionStore(tmp_path / "s.jsonl")
        m = InsightMeta(topic="tt", text="x", evidence_count=1, confidence=0.4)
        m.recompute_id_range()
        first = store.upsert_pending(m)
        store.update_status(first.id, "rejected", reject_reason="틀림")

        # 같은 토픽으로 다시 upsert → 새로운 pending 행이 생긴다.
        m.text = "다시"
        second = store.upsert_pending(m)
        assert second.id != first.id
        assert second.status == "pending"
        # 두 행 모두 디스크에 남아 있다 (audit).
        all_ids = [s.id for s in store.load()]
        assert first.id in all_ids
        assert second.id in all_ids

    def test_update_status_invalid_value_raises(self, tmp_path: Path):
        store = SuggestionStore(tmp_path / "s.jsonl")
        m = InsightMeta(topic="t", text="x", evidence_count=1, confidence=0.4)
        m.recompute_id_range()
        s = store.upsert_pending(m)
        with pytest.raises(ValueError):
            store.update_status(s.id, "bogus")

    def test_serialization_roundtrip(self, tmp_path: Path):
        store = SuggestionStore(tmp_path / "s.jsonl")
        m = InsightMeta(
            topic="t",
            text="x",
            evidence_count=2,
            confidence=0.55,
            source_msg_ids=[1, 2, 3],
        )
        m.recompute_id_range()
        s = store.upsert_pending(m)
        store.update_status(s.id, "edited", edited_text="고친 텍스트")

        # Re-open store from same path, ensure state survives.
        store2 = SuggestionStore(tmp_path / "s.jsonl")
        loaded = store2.get(s.id)
        assert loaded is not None
        assert loaded.status == "edited"
        assert loaded.edited_text == "고친 텍스트"
        assert loaded.applied_text == "고친 텍스트"


# ----------------------------------------------------------------------
# BlocklistStore
# ----------------------------------------------------------------------


class TestBlocklistStore:
    def test_blocked_topic_normalized(self, tmp_path: Path):
        bl = BlocklistStore(tmp_path / "bl.jsonl")
        bl.add("맥북에어 가격!", reason="not interesting")
        # 표기가 달라도 정규형이 같으면 차단.
        assert bl.is_blocked("맥북에어가격")
        assert bl.is_blocked("  맥북에어 가격 ")
        # 다른 토픽은 차단 X.
        assert not bl.is_blocked("정치뉴스")

    def test_empty_topic_is_noop(self, tmp_path: Path):
        bl = BlocklistStore(tmp_path / "bl.jsonl")
        bl.add("", reason="empty")
        bl.add("---", reason="punct only")
        assert bl.list_all() == []


# ----------------------------------------------------------------------
# DreamingPipeline.apply_insight_meta — dry-run 분기 검증 (DoD #1, #2)
# ----------------------------------------------------------------------


def _make_pipeline_in_dryrun_mode(tmp_path: Path) -> tuple[DreamingPipeline, Path, Path, Path]:
    """suggestions/blocklist 가 활성화된 dry-run 모드 파이프라인을 만든다.

    Returns:
        (pipeline, user_md_path, suggestions_path, blocklist_path)
    """
    db = tmp_path / "test.db"
    store = ConversationStore(db)
    memory_file = tmp_path / "MEMORY.md"
    memory_file.write_text(
        "# Memory\n\n"
        "<!-- managed:dreaming:journal -->\n"
        "<!-- /managed:dreaming:journal -->\n",
        encoding="utf-8",
    )
    user_file = tmp_path / "USER.md"
    user_file.write_text(
        "# User\n\n"
        "<!-- managed:dreaming:insights -->\n"
        "<!-- /managed:dreaming:insights -->\n",
        encoding="utf-8",
    )
    suggestions_file = tmp_path / "suggestions.jsonl"
    blocklist_file = tmp_path / "blocklist.jsonl"
    insights_file = tmp_path / "insights.jsonl"

    pipeline = DreamingPipeline(
        conversation_store=store,
        memory_file=memory_file,
        user_file=user_file,
        insights_file=insights_file,
        insight_promotion_threshold=3,
        suggestions_file=suggestions_file,
        blocklist_file=blocklist_file,
        auto_promote_confidence=0.7,
        auto_promote_evidence_count=3,
    )
    return pipeline, user_file, suggestions_file, blocklist_file


class TestDryRunRouting:
    def test_single_observation_queued_not_applied(self, tmp_path: Path):
        """DoD #1 + #2: 1회 관측은 confidence 0.4 → 큐에 들어가고 USER.md 변화 없음."""
        pipeline, user_md, sugg_path, _ = _make_pipeline_in_dryrun_mode(tmp_path)
        before = user_md.read_text(encoding="utf-8")

        meta_items = [{"topic": "정치뉴스", "text": "정치 뉴스에 관심"}]
        changed, promoted = pipeline.apply_insight_meta(
            meta_items, source_msg_ids=[1, 2]
        )
        assert len(changed) == 1
        # auto-promote 미달성 → promoted (=auto-apply) 비어 있음.
        assert promoted == []
        # 큐에 적재됨.
        store = SuggestionStore(sugg_path)
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].topic == "정치뉴스"
        assert pending[0].evidence_count == 1
        assert pending[0].confidence == pytest.approx(0.4)
        # USER.md 는 무관 — apply_insight_meta 자체가 USER.md 를 건드리지 않는다.
        assert user_md.read_text(encoding="utf-8") == before

    def test_auto_promote_only_when_both_thresholds_met(self, tmp_path: Path):
        """DoD #2: confidence ≥ 0.7 AND evidence_count ≥ 3 동시에 — 한쪽만이면 큐."""
        pipeline, _, sugg_path, _ = _make_pipeline_in_dryrun_mode(tmp_path)

        # 같은 topic 으로 3회 관측 — confidence 0.7, evidence_count 3 → auto-promote.
        for _ in range(3):
            changed, promoted = pipeline.apply_insight_meta(
                [{"topic": "ai트렌드", "text": "AI 트렌드 본다"}],
                source_msg_ids=[100],
            )
        # 3회차 시점에서 변경됨 + 자동 적용 대상으로 분류.
        assert len(changed) == 1
        assert len(promoted) == 1
        assert promoted[0].evidence_count == 3
        assert promoted[0].confidence == pytest.approx(0.7)
        # 자동 적용 대상은 큐에 들어가지 않는다.
        store = SuggestionStore(sugg_path)
        topics_in_queue = [normalize_topic(s.topic) for s in store.list_pending()]
        assert "ai트렌드" not in topics_in_queue

    def test_auto_promote_blocked_when_only_confidence_high(self, tmp_path: Path):
        """confidence 만 임계 도달, evidence_count 미달 → 큐로 들어간다.

        실험: auto_promote_evidence_count=5 로 올려서 3회로는 부족하게.
        """
        db = tmp_path / "test.db"
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text(
            "# M\n\n<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n",
            encoding="utf-8",
        )
        user_file = tmp_path / "USER.md"
        user_file.write_text(
            "# U\n\n<!-- managed:dreaming:insights -->\n<!-- /managed:dreaming:insights -->\n",
            encoding="utf-8",
        )
        sugg_path = tmp_path / "s.jsonl"
        pipeline = DreamingPipeline(
            conversation_store=ConversationStore(db),
            memory_file=memory_file,
            user_file=user_file,
            insights_file=tmp_path / "insights.jsonl",
            insight_promotion_threshold=3,
            suggestions_file=sugg_path,
            blocklist_file=tmp_path / "bl.jsonl",
            auto_promote_confidence=0.7,
            auto_promote_evidence_count=5,  # 3회로는 부족
        )
        for _ in range(3):
            _, promoted = pipeline.apply_insight_meta(
                [{"topic": "트렌드", "text": "트렌드"}], source_msg_ids=[1]
            )
        # confidence 0.7 도달했지만 evidence_count 3 < 5 → 큐로.
        assert promoted == []
        pending = SuggestionStore(sugg_path).list_pending()
        assert len(pending) == 1


# ----------------------------------------------------------------------
# Reject → blocklist → 차단 루프 (DoD #3 — E2E 핵심)
# ----------------------------------------------------------------------


class TestRejectBlocklistLoop:
    def test_blocklisted_topic_filtered_before_merge(self, tmp_path: Path):
        """블록리스트 추가 후 같은 topic 의 관측은 sidecar/큐 어디에도 들어가지 않는다."""
        pipeline, _, sugg_path, bl_path = _make_pipeline_in_dryrun_mode(tmp_path)

        # 1) 첫 관측 → 큐에 적재.
        pipeline.apply_insight_meta(
            [{"topic": "스팸토픽", "text": "정크 정보"}], source_msg_ids=[1]
        )
        sugg_store = SuggestionStore(sugg_path)
        pending = sugg_store.list_pending()
        assert len(pending) == 1
        sid = pending[0].id

        # 2) reject → 블록리스트에 추가 + suggestion 행 status=rejected.
        bl_store = BlocklistStore(bl_path)
        bl_store.add("스팸토픽", reason="스팸")
        sugg_store.update_status(sid, "rejected", reject_reason="스팸")

        # 3) 다음 사이클에 같은 topic 재관측 → 필터링되어 sidecar/큐 변화 없음.
        insights_path = tmp_path / "insights.jsonl"
        insights_before = InsightStore(insights_path).load()

        changed, promoted = pipeline.apply_insight_meta(
            [{"topic": "스팸토픽", "text": "다시 정크"}], source_msg_ids=[2]
        )
        assert changed == []
        assert promoted == []

        # sidecar 도 변화 없음 (스팸토픽 evidence_count 가 늘지 않았다).
        insights_after = InsightStore(insights_path).load()
        assert insights_after.get(normalize_topic("스팸토픽")) is None or (
            insights_after.get(normalize_topic("스팸토픽")).evidence_count
            == insights_before.get(normalize_topic("스팸토픽"), InsightMeta(topic="x", text="x")).evidence_count
        )
        # 큐에도 새 pending 이 안 생긴다.
        new_pending = SuggestionStore(sugg_path).list_pending()
        assert new_pending == []  # 위에서 reject 한 행만 있고 새 pending 은 없음.


# ----------------------------------------------------------------------
# 레거시 모드 호환 — suggestion_store 미주입이면 기존 동작 그대로
# ----------------------------------------------------------------------


class TestLegacyCompatibility:
    def test_legacy_mode_uses_is_promoted(self, tmp_path: Path):
        """suggestion_store 미주입 시 promoted 는 ``is_promoted`` 기준 (BIZ-73 호환)."""
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text(
            "# M\n\n<!-- managed:dreaming:journal -->\n<!-- /managed:dreaming:journal -->\n",
            encoding="utf-8",
        )
        user_file = tmp_path / "USER.md"
        user_file.write_text(
            "# U\n\n<!-- managed:dreaming:insights -->\n<!-- /managed:dreaming:insights -->\n",
            encoding="utf-8",
        )
        pipeline = DreamingPipeline(
            conversation_store=ConversationStore(tmp_path / "c.db"),
            memory_file=memory_file,
            user_file=user_file,
            insights_file=tmp_path / "insights.jsonl",
            insight_promotion_threshold=3,
            # suggestions_file/blocklist_file 미지정 → 레거시 모드.
        )
        for _ in range(3):
            _, promoted = pipeline.apply_insight_meta(
                [{"topic": "토픽", "text": "x"}], source_msg_ids=[1]
            )
        assert len(promoted) == 1  # is_promoted 기준


# ----------------------------------------------------------------------
# DreamingPipeline.run() 에서 USER.md 가 큐 우회 항목만 받는지 (DoD #1 통합)
# ----------------------------------------------------------------------


class TestRunDryRunIntegration:
    @pytest.mark.asyncio
    async def test_run_routes_to_queue_not_user_md(self, tmp_path: Path):
        """run() 실행 시 단발 인사이트는 USER.md 가 아니라 큐에 들어간다."""
        pipeline, user_md, sugg_path, _ = _make_pipeline_in_dryrun_mode(tmp_path)

        # 대화 메시지 1건 추가 — collect_unprocessed 가 가져갈 수 있게.
        conv = pipeline._store
        conv.add_message(
            ConversationMessage(
                role=MessageRole.USER, content="정치 뉴스 보여줘", channel="cli"
            )
        )

        # LLM 응답을 mock — user_insights 와 user_insights_meta 를 함께 돌려준다.
        pipeline._router = MagicMock()
        pipeline._router.send = AsyncMock(return_value=MagicMock(
            text='{"memory": "## 2026-05-03\\n- conv", "user_insights": "- 정치 뉴스에 관심", '
                 '"user_insights_meta": [{"topic": "정치뉴스", "text": "정치 뉴스에 관심"}], '
                 '"soul_updates": "", "agent_updates": ""}'
        ))

        await pipeline.run()

        # USER.md 의 dreaming insights 섹션 안쪽이 비어 있어야 한다 — 큐로 갔으니까.
        body = user_md.read_text(encoding="utf-8")
        assert "정치 뉴스에 관심" not in body
        # 대신 suggestion 큐에 1건.
        pending = SuggestionStore(sugg_path).list_pending()
        assert len(pending) == 1
        assert pending[0].topic == "정치뉴스"
