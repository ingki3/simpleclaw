"""Tests for the dreaming suggestion queue + reject blocklist (BIZ-79).

검증 범위:
- SuggestionItem 직렬화 / from_insight / to_insight 라운드트립
- SuggestionStore upsert/load/list_pending/mark_status/update_text
- InsightBlocklist add/is_blocked/list_topics
- AutoPromotePolicy AND 결합 의미
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from simpleclaw.memory.insights import InsightMeta
from simpleclaw.memory.suggestions import (
    SUGGESTION_STATUS_ACCEPTED,
    SUGGESTION_STATUS_PENDING,
    SUGGESTION_STATUS_REJECTED,
    AutoPromotePolicy,
    InsightBlocklist,
    SuggestionItem,
    SuggestionStore,
)


class TestSuggestionItem:
    def test_roundtrip_preserves_all_fields(self):
        """to_dict/from_dict 라운드트립이 모든 필드를 보존한다."""
        original = SuggestionItem(
            topic="맥북에어가격",
            text="맥북 에어 가격 조회",
            evidence_count=2,
            confidence=0.55,
            source_msg_ids=[10, 11, 12],
            start_msg_id=10,
            end_msg_id=12,
            status=SUGGESTION_STATUS_PENDING,
        )
        d = original.to_dict()
        restored = SuggestionItem.from_dict(d)
        assert restored.topic == original.topic
        assert restored.text == original.text
        assert restored.evidence_count == original.evidence_count
        assert restored.confidence == original.confidence
        assert restored.source_msg_ids == original.source_msg_ids
        assert restored.start_msg_id == original.start_msg_id
        assert restored.end_msg_id == original.end_msg_id
        assert restored.status == original.status

    def test_from_insight_carries_all_metadata(self):
        """InsightMeta → SuggestionItem 변환이 모든 메타를 옮긴다."""
        meta = InsightMeta(
            topic="정치뉴스",
            text="정치 뉴스 1회 관측",
            evidence_count=1,
            confidence=0.4,
            source_msg_ids=[5],
            start_msg_id=5,
            end_msg_id=5,
        )
        item = SuggestionItem.from_insight(meta)
        assert item.topic == meta.topic
        assert item.text == meta.text
        assert item.evidence_count == meta.evidence_count
        assert item.confidence == meta.confidence
        assert item.source_msg_ids == meta.source_msg_ids
        assert item.status == SUGGESTION_STATUS_PENDING

    def test_to_insight_inverse(self):
        """SuggestionItem → InsightMeta 변환이 큐 메타만 떼고 나머지를 보존한다."""
        item = SuggestionItem(
            topic="x",
            text="y",
            evidence_count=4,
            confidence=0.85,
            source_msg_ids=[1, 2],
            start_msg_id=1,
            end_msg_id=2,
            status=SUGGESTION_STATUS_ACCEPTED,
        )
        meta = item.to_insight()
        assert meta.topic == "x"
        assert meta.evidence_count == 4
        assert meta.source_msg_ids == [1, 2]
        # status 같은 큐 전용 필드는 InsightMeta 에 존재하지 않음 — 변환에서 자연 탈락.
        assert not hasattr(meta, "status")


class TestSuggestionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return SuggestionStore(tmp_path / "insight_suggestions.jsonl")

    def test_load_missing_file_returns_empty(self, store):
        assert store.load() == {}

    def test_upsert_then_load(self, store):
        item = SuggestionItem(topic="t1", text="t1 text")
        store.upsert_pending(item)
        loaded = store.load()
        assert "t1" in loaded
        assert loaded["t1"].text == "t1 text"
        assert loaded["t1"].status == SUGGESTION_STATUS_PENDING

    def test_upsert_normalizes_topic_for_key(self, store):
        """공백/대소문자 차이가 있어도 같은 행으로 병합된다."""
        store.upsert_pending(SuggestionItem(topic="Mac Air", text="v1"))
        # 정규형은 \"macair\". 공백/대소문자 다른 입력도 같은 키.
        store.upsert_pending(SuggestionItem(topic="macair", text="v2"))
        loaded = store.load()
        # 같은 정규형 키로 1건만 존재. 마지막 upsert 가 유효.
        assert len(loaded) == 1
        assert loaded["macair"].text == "v2"

    def test_list_pending_filters_by_status(self, store):
        store.upsert_pending(SuggestionItem(topic="a", text="a"))
        store.upsert_pending(SuggestionItem(topic="b", text="b"))
        store.upsert_pending(SuggestionItem(topic="c", text="c"))
        store.mark_status("b", SUGGESTION_STATUS_ACCEPTED)
        store.mark_status("c", SUGGESTION_STATUS_REJECTED)

        pending = store.list_pending()
        topics = [item.topic for item in pending]
        # 결정된 항목은 큐 화면 default 표시에서 제외 — accept/reject audit 은 sidecar 에 남는다.
        assert "a" in topics
        assert "b" not in topics
        assert "c" not in topics

    def test_list_pending_sorted_recent_first(self, store):
        """가장 최근 제안이 위로 — 운영 직관(최신 검수 우선)."""
        old = SuggestionItem(topic="old", text="old")
        old.suggested_at = datetime(2026, 5, 1, 10, 0, 0)
        new = SuggestionItem(topic="new", text="new")
        new.suggested_at = datetime(2026, 5, 3, 10, 0, 0)
        # 의도적으로 '오래된 항목 먼저' 저장 — 정렬은 list_pending 내부 책임.
        store.upsert_pending(old)
        store.upsert_pending(new)

        pending = store.list_pending()
        assert pending[0].topic == "new"
        assert pending[1].topic == "old"

    def test_mark_status_persists(self, store):
        store.upsert_pending(SuggestionItem(topic="t", text="x"))
        result = store.mark_status("t", SUGGESTION_STATUS_REJECTED)
        assert result is not None
        assert result.status == SUGGESTION_STATUS_REJECTED
        # 디스크 라운드트립 검증
        loaded = store.load()
        assert loaded["t"].status == SUGGESTION_STATUS_REJECTED

    def test_mark_status_unknown_topic_returns_none(self, store):
        assert store.mark_status("nope", SUGGESTION_STATUS_REJECTED) is None

    def test_update_text_keeps_status(self, store):
        """edit 액션은 본문만 갱신하고 status 는 손대지 않는다."""
        store.upsert_pending(SuggestionItem(topic="t", text="orig"))
        result = store.update_text("t", "edited text")
        assert result is not None
        assert result.text == "edited text"
        assert result.status == SUGGESTION_STATUS_PENDING

    def test_upsert_preserves_decided_status(self, store):
        """이미 accept/reject 결정된 항목에 같은 topic 이 다시 들어와도 결정을 유지한다.

        DoD §B 와 직결되는 핵심 가드: 운영자가 한 번 reject 한 인사이트가 다음
        사이클 재관측만으로 다시 pending 으로 되돌아가면 안 된다. 큐의 의미가 깨진다.
        """
        store.upsert_pending(SuggestionItem(topic="t", text="v1"))
        store.mark_status("t", SUGGESTION_STATUS_REJECTED)
        # 다음 사이클에 같은 topic 이 다시 들어옴 — 상태는 rejected 그대로.
        store.upsert_pending(
            SuggestionItem(topic="t", text="v2", evidence_count=2, confidence=0.55)
        )
        loaded = store.load()
        assert loaded["t"].status == SUGGESTION_STATUS_REJECTED
        # evidence/source 같은 누적 필드는 갱신되어 audit 으로 남음.
        assert loaded["t"].evidence_count == 2
        assert loaded["t"].confidence == 0.55


class TestInsightBlocklist:
    @pytest.fixture
    def blocklist(self, tmp_path):
        return InsightBlocklist(tmp_path / "insight_blocklist.jsonl")

    def test_initially_empty(self, blocklist):
        assert blocklist.is_blocked("anything") is False
        assert blocklist.list_topics() == []

    def test_add_blocks_subsequent_check(self, blocklist):
        blocklist.add("정치뉴스", reason="user_rejected")
        assert blocklist.is_blocked("정치뉴스") is True
        # 정규형 일치도 동작 — \"정치 뉴스\" 같은 표기 차이도 차단된다.
        assert blocklist.is_blocked("정치 뉴스") is True

    def test_add_idempotent(self, blocklist):
        """같은 topic 을 여러 번 add 해도 중복 행이 쌓이지 않는다."""
        blocklist.add("t1")
        blocklist.add("t1")
        blocklist.add("t1")
        assert len(blocklist.list_topics()) == 1

    def test_persists_across_instances(self, blocklist, tmp_path):
        blocklist.add("t1")
        # 새 instance 로도 같은 디스크 상태 읽힘 — JSONL atomic-rename 동작 검증.
        reopened = InsightBlocklist(tmp_path / "insight_blocklist.jsonl")
        assert reopened.is_blocked("t1") is True


class TestAutoPromotePolicy:
    def test_both_conditions_required(self):
        """confidence AND evidence_count 동시 충족만 자동 승격."""
        policy = AutoPromotePolicy(confidence=0.7, evidence_count=3)
        # 둘 다 충족
        meta_pass = InsightMeta(
            topic="t", text="t", evidence_count=3, confidence=0.7
        )
        assert policy.should_auto_promote(meta_pass) is True
        # confidence 만 부족
        meta_fail_conf = InsightMeta(
            topic="t", text="t", evidence_count=5, confidence=0.69
        )
        assert policy.should_auto_promote(meta_fail_conf) is False
        # evidence 만 부족
        meta_fail_evidence = InsightMeta(
            topic="t", text="t", evidence_count=2, confidence=0.95
        )
        assert policy.should_auto_promote(meta_fail_evidence) is False
