"""메모리 분포 통계 + RAG 로그 분석 단위 테스트 (BIZ-29).

검증 범위:
- ``ConversationStore`` 분포 헬퍼: count_with_embedding / count_clustered /
  count_unclustered_with_embedding / embedding_dimension_distribution / cluster_member_counts
- ``compute_distribution_stats`` 집계 데이터클래스의 모든 필드
- ``analyze_rag_logs`` 의 일자별 합산·hit_rate 계산·손상 라인 무시 동작
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.memory.stats import (
    analyze_rag_logs,
    compute_distribution_stats,
)


@pytest.fixture
def store(tmp_path):
    return ConversationStore(tmp_path / "stats.db")


def _msg(content: str, *, tokens: int = 0) -> ConversationMessage:
    return ConversationMessage(
        role=MessageRole.USER, content=content, token_count=tokens
    )


# ------------------------------------------------------------------
# ConversationStore 분포 헬퍼
# ------------------------------------------------------------------


class TestStoreDistributionHelpers:
    def test_empty_store(self, store):
        assert store.count_with_embedding() == 0
        assert store.count_clustered() == 0
        assert store.count_unclustered_with_embedding() == 0
        assert store.embedding_dimension_distribution() == {}
        assert store.cluster_member_counts() == {}

    def test_count_with_embedding(self, store):
        m1 = store.add_message(_msg("a"))
        store.add_message(_msg("b"))  # no embedding
        store.add_embedding(m1, [1.0, 0.0])
        assert store.count_with_embedding() == 1

    def test_count_clustered_and_unclustered(self, store):
        cid = store.create_cluster(label="x", centroid=[1.0, 0.0])
        m_emb_clustered = store.add_message(_msg("a"))
        m_emb_only = store.add_message(_msg("b"))
        store.add_message(_msg("c"))  # no embedding, no cluster

        store.add_embedding(m_emb_clustered, [1.0, 0.0])
        store.add_embedding(m_emb_only, [0.0, 1.0])
        store.assign_cluster(m_emb_clustered, cid)

        assert store.count_clustered() == 1
        assert store.count_unclustered_with_embedding() == 1

    def test_embedding_dimension_distribution_uniform(self, store):
        m1 = store.add_message(_msg("a"))
        m2 = store.add_message(_msg("b"))
        store.add_embedding(m1, [0.1, 0.2, 0.3])
        store.add_embedding(m2, [0.4, 0.5, 0.6])
        assert store.embedding_dimension_distribution() == {3: 2}

    def test_embedding_dimension_distribution_mixed(self, store):
        m1 = store.add_message(_msg("a"))
        m2 = store.add_message(_msg("b"))
        store.add_embedding(m1, [1.0, 0.0])  # dim 2
        store.add_embedding(m2, [1.0, 0.0, 0.0, 0.0])  # dim 4
        # 모델 교체로 차원이 섞인 시나리오 — 두 dim 모두 노출
        assert store.embedding_dimension_distribution() == {2: 1, 4: 1}

    def test_cluster_member_counts(self, store):
        c1 = store.create_cluster(label="a", centroid=[1.0])
        c2 = store.create_cluster(label="b", centroid=[1.0])
        m1 = store.add_message(_msg("x"))
        m2 = store.add_message(_msg("y"))
        m3 = store.add_message(_msg("z"))
        store.assign_cluster(m1, c1)
        store.assign_cluster(m2, c1)
        store.assign_cluster(m3, c2)
        assert store.cluster_member_counts() == {c1: 2, c2: 1}


# ------------------------------------------------------------------
# compute_distribution_stats
# ------------------------------------------------------------------


class TestComputeDistributionStats:
    def test_empty(self, store):
        stats = compute_distribution_stats(store)
        assert stats.total_messages == 0
        assert stats.messages_with_embedding == 0
        assert stats.coverage_percent == 0.0
        assert stats.cluster_count == 0
        assert stats.clustered_messages == 0
        assert stats.unclustered_with_embedding == 0
        assert stats.cluster_distributions == []
        assert stats.embedding_dimensions == {}
        assert stats.has_dimension_inconsistency is False

    def test_full_population(self, store):
        # 4개 메시지 — 임베딩 3개, 클러스터 부착 2개
        m1 = store.add_message(_msg("a"))
        m2 = store.add_message(_msg("b"))
        m3 = store.add_message(_msg("c"))
        store.add_message(_msg("d"))  # no embedding, no cluster
        store.add_embedding(m1, [1.0, 0.0])
        store.add_embedding(m2, [0.0, 1.0])
        store.add_embedding(m3, [1.0, 1.0])

        cid = store.create_cluster(
            label="topic", centroid=[1.0, 0.0], member_count=99,  # 캐시 drift
        )
        store.assign_cluster(m1, cid)
        store.assign_cluster(m2, cid)

        stats = compute_distribution_stats(store)
        assert stats.total_messages == 4
        assert stats.messages_with_embedding == 3
        assert stats.coverage_percent == pytest.approx(75.0)
        assert stats.cluster_count == 1
        assert stats.clustered_messages == 2
        assert stats.unclustered_with_embedding == 1  # m3
        assert stats.embedding_dimensions == {2: 3}
        assert stats.has_dimension_inconsistency is False

        # 클러스터 분포 + drift
        assert len(stats.cluster_distributions) == 1
        cd = stats.cluster_distributions[0]
        assert cd.cluster_id == cid
        assert cd.label == "topic"
        assert cd.actual_member_count == 2
        assert cd.stored_member_count == 99
        assert cd.drift == 97  # stored - actual

        # 멤버 통계 — 단일 클러스터라 모두 동일
        assert stats.members_min == 2
        assert stats.members_max == 2
        assert stats.members_mean == pytest.approx(2.0)
        assert stats.members_median == 2.0

    def test_dimension_inconsistency_flag(self, store):
        m1 = store.add_message(_msg("a"))
        m2 = store.add_message(_msg("b"))
        store.add_embedding(m1, [1.0, 0.0])
        store.add_embedding(m2, [1.0, 0.0, 0.0])

        stats = compute_distribution_stats(store)
        assert stats.has_dimension_inconsistency is True
        assert stats.embedding_dimensions == {2: 1, 3: 1}

    def test_to_dict_serializable(self, store):
        store.add_message(_msg("a"))
        stats = compute_distribution_stats(store)
        # JSON 직렬화 가능해야 한다(대시보드 노출 전제)
        json.dumps(stats.to_dict())


# ------------------------------------------------------------------
# analyze_rag_logs
# ------------------------------------------------------------------


def _write_log(log_dir, date_str: str, entries: list[dict]) -> None:
    """테스트용 JSONL 로그 작성 헬퍼."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"execution_{date_str}.log"
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TestAnalyzeRagLogs:
    def test_empty_directory(self, tmp_path):
        result = analyze_rag_logs(tmp_path / "logs", days=7)
        assert result.total_calls == 0
        assert result.total_hits == 0
        assert result.daily == []
        assert result.hit_rate == 0.0
        assert result.avg_recalled_tokens == 0.0

    def test_aggregates_single_day(self, tmp_path):
        today = datetime(2026, 5, 1, 12, 0, 0)
        date_str = today.strftime("%Y%m%d")
        _write_log(tmp_path, date_str, [
            {
                "action_type": "rag_retrieve",
                "details": {"hit": True, "recalled_messages": 3, "recalled_tokens": 120},
            },
            {
                "action_type": "rag_retrieve",
                "details": {"hit": False, "recalled_messages": 0, "recalled_tokens": 0},
            },
            # 다른 action_type은 무시되어야 함
            {"action_type": "skill_execute", "details": {"hit": True}},
        ])

        result = analyze_rag_logs(tmp_path, days=7, today=today)
        assert result.total_calls == 2
        assert result.total_hits == 1
        assert result.total_recalled_tokens == 120
        assert result.hit_rate == pytest.approx(0.5)
        assert result.avg_recalled_tokens == pytest.approx(60.0)
        assert len(result.daily) == 1
        d = result.daily[0]
        assert d.date == date_str
        assert d.recalled_messages_sum == 3

    def test_window_excludes_older_logs(self, tmp_path):
        today = datetime(2026, 5, 10, 0, 0, 0)
        # 윈도우 안: 5/9
        _write_log(tmp_path, "20260509", [
            {"action_type": "rag_retrieve", "details": {"hit": True, "recalled_tokens": 100}},
        ])
        # 윈도우 밖: 5/1 (10일 전)
        _write_log(tmp_path, "20260501", [
            {"action_type": "rag_retrieve", "details": {"hit": True, "recalled_tokens": 999}},
        ])

        result = analyze_rag_logs(tmp_path, days=3, today=today)
        assert result.total_calls == 1
        assert result.total_recalled_tokens == 100

    def test_skips_corrupt_lines(self, tmp_path):
        today = datetime(2026, 5, 1, 12, 0, 0)
        date_str = today.strftime("%Y%m%d")
        path = tmp_path / f"execution_{date_str}.log"
        tmp_path.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not-json\n")
            f.write(json.dumps({
                "action_type": "rag_retrieve",
                "details": {"hit": True, "recalled_tokens": 50},
            }) + "\n")
            f.write("\n")  # 빈 줄
            f.write("{broken json")  # 라인 단편

        result = analyze_rag_logs(tmp_path, days=1, today=today)
        assert result.total_calls == 1
        assert result.total_recalled_tokens == 50

    def test_daily_sorted_ascending(self, tmp_path):
        today = datetime(2026, 5, 3, 0, 0, 0)
        for day_offset in (0, 1, 2):
            d = today - timedelta(days=day_offset)
            _write_log(tmp_path, d.strftime("%Y%m%d"), [
                {"action_type": "rag_retrieve", "details": {"hit": True, "recalled_tokens": 10}},
            ])

        result = analyze_rag_logs(tmp_path, days=3, today=today)
        dates = [d.date for d in result.daily]
        assert dates == sorted(dates)

    def test_to_dict_serializable(self, tmp_path):
        today = datetime(2026, 5, 1)
        _write_log(tmp_path, today.strftime("%Y%m%d"), [
            {"action_type": "rag_retrieve", "details": {"hit": False}},
        ])
        result = analyze_rag_logs(tmp_path, days=7, today=today)
        json.dumps(result.to_dict())  # 예외 없이 직렬화 가능
