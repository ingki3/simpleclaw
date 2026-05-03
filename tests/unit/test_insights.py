"""Unit tests for BIZ-73 — Insight Schema & Confidence.

DoD 회귀 가드:
1. 단발 관측 confidence 는 0.4 를 초과하지 않는다.
2. promotion_threshold 회 누적 시 승격선(0.7)에 도달한다.
3. 같은 topic 으로 다시 관측되면 evidence_count 가 가산되고 last_seen / source_msg_ids 가 갱신된다.

BIZ-77 (F: Source Linkage) 회귀 가드:
4. ``InsightMeta`` 에 ``start_msg_id`` / ``end_msg_id`` 매핑이 존재하고 ``source_msg_ids`` 의 min/max 와 일치한다.
5. 구버전 sidecar (start/end 필드 없음) 도 ``source_msg_ids`` 로부터 자동 보강된다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from simpleclaw.memory.insights import (
    InsightMeta,
    InsightStore,
    compute_confidence,
    is_promoted,
    merge_insights,
    normalize_topic,
)


# ----------------------------------------------------------------------
# normalize_topic
# ----------------------------------------------------------------------

class TestNormalizeTopic:
    def test_strips_whitespace_and_punct(self):
        assert normalize_topic("  맥북에어 가격! ") == "맥북에어가격"

    def test_lowercases_ascii(self):
        assert normalize_topic("MacBook Air") == "macbookair"

    def test_empty_input(self):
        assert normalize_topic("") == ""
        assert normalize_topic("   ") == ""
        # 정규화 후 빈 문자열이 되는 케이스 (기호만)
        assert normalize_topic("---") == ""

    def test_idempotent(self):
        once = normalize_topic("정치 뉴스 요약")
        twice = normalize_topic(once)
        assert once == twice == "정치뉴스요약"


# ----------------------------------------------------------------------
# compute_confidence + is_promoted (DoD 1, 2)
# ----------------------------------------------------------------------

class TestComputeConfidence:
    def test_single_observation_capped_at_04(self):
        """DoD #1: 단발 관측은 confidence ≤ 0.4."""
        # 다양한 promotion_threshold 에서 모두 0.4 캡 유지.
        for threshold in [1, 2, 3, 5, 10]:
            assert compute_confidence(1, threshold) == 0.4

    def test_zero_observations_is_zero(self):
        assert compute_confidence(0, 3) == 0.0

    def test_promoted_at_threshold(self):
        """DoD #2: promotion_threshold 회 도달 시 confidence == 0.7 (승격선)."""
        assert compute_confidence(3, 3) == 0.7
        assert compute_confidence(5, 5) == 0.7

    def test_below_threshold_interpolated(self):
        # threshold=3 에서 2회는 0.4 와 0.7 의 정확한 중간(0.55).
        assert compute_confidence(2, 3) == pytest.approx(0.55)

    def test_above_threshold_grows_to_one(self):
        # threshold=3 일 때 6회(=2*threshold)에 1.0 도달.
        assert compute_confidence(6, 3) == 1.0
        # 더 많이 관측되어도 1.0 캡.
        assert compute_confidence(100, 3) == 1.0

    def test_threshold_one_edge(self):
        """promotion_threshold == 1 (즉시 승격) 엣지에서도 1회는 여전히 0.4 캡."""
        assert compute_confidence(1, 1) == 0.4
        # 2회부터 0.7 시작.
        assert compute_confidence(2, 1) == 0.7
        assert compute_confidence(3, 1) == pytest.approx(0.75)

    def test_invalid_threshold_normalized(self):
        # threshold < 1 은 1로 강제 — 안정성 가드.
        assert compute_confidence(2, 0) == 0.7
        assert compute_confidence(2, -5) == 0.7


class TestIsPromoted:
    def test_below_threshold_not_promoted(self):
        meta = InsightMeta(topic="t", text="x", evidence_count=2)
        assert not is_promoted(meta, promotion_threshold=3)

    def test_at_threshold_is_promoted(self):
        meta = InsightMeta(topic="t", text="x", evidence_count=3)
        assert is_promoted(meta, promotion_threshold=3)

    def test_single_observation_not_promoted(self):
        meta = InsightMeta(topic="t", text="x", evidence_count=1)
        assert not is_promoted(meta, promotion_threshold=3)


# ----------------------------------------------------------------------
# InsightStore (load / save_all)
# ----------------------------------------------------------------------

class TestInsightStore:
    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        store = InsightStore(tmp_path / "missing.jsonl")
        assert store.load() == {}

    def test_save_then_load_roundtrip(self, tmp_path: Path):
        store = InsightStore(tmp_path / "insights.jsonl")
        meta = InsightMeta(
            topic="맥북에어가격",
            text="맥북에어 15인치 가격을 1회 조회함",
            evidence_count=1,
            confidence=0.4,
            first_seen=datetime(2026, 4, 28, 10, 0, 0),
            last_seen=datetime(2026, 4, 28, 10, 0, 0),
            source_msg_ids=[123, 124],
        )
        store.save_all({normalize_topic(meta.topic): meta})

        loaded = store.load()
        assert "맥북에어가격" in loaded
        got = loaded["맥북에어가격"]
        assert got.text == meta.text
        assert got.evidence_count == 1
        assert got.confidence == 0.4
        assert got.source_msg_ids == [123, 124]
        assert got.first_seen == meta.first_seen

    def test_load_skips_malformed_lines(self, tmp_path: Path):
        path = tmp_path / "insights.jsonl"
        # 첫 줄: 손상, 둘째 줄: 정상.
        path.write_text(
            "this is not json\n"
            + json.dumps(
                {
                    "topic": "정치뉴스",
                    "text": "정치 뉴스를 1회 요약함",
                    "evidence_count": 1,
                    "confidence": 0.4,
                    "first_seen": "2026-04-28T10:00:00",
                    "last_seen": "2026-04-28T10:00:00",
                    "source_msg_ids": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        loaded = InsightStore(path).load()
        assert list(loaded.keys()) == ["정치뉴스"]

    def test_save_is_atomic_no_tmp_left(self, tmp_path: Path):
        store = InsightStore(tmp_path / "insights.jsonl")
        store.save_all({"x": InsightMeta(topic="x", text="y")})
        # tmp 파일은 rename 후 사라져 있어야 한다.
        assert not (tmp_path / "insights.jsonl.tmp").exists()


# ----------------------------------------------------------------------
# merge_insights (DoD 3)
# ----------------------------------------------------------------------

class TestMergeInsights:
    def test_new_topic_added_with_capped_confidence(self):
        existing: dict = {}
        new_obs = [InsightMeta(topic="정치뉴스", text="정치 뉴스 1회 요약")]
        merged, changed = merge_insights(existing, new_obs, promotion_threshold=3)

        assert "정치뉴스" in merged
        assert merged["정치뉴스"].evidence_count == 1
        # DoD #1 회귀 가드 — 단발 관측은 0.4 이하로 캡.
        assert merged["정치뉴스"].confidence == 0.4
        assert len(changed) == 1

    def test_same_topic_increments_evidence(self):
        """DoD #3: 동일 topic 이 다시 들어오면 evidence_count++ + last_seen 갱신."""
        old_time = datetime(2026, 4, 28, 10, 0, 0)
        existing = {
            "맥북에어가격": InsightMeta(
                topic="맥북에어가격",
                text="맥북에어 가격 조회",
                evidence_count=1,
                confidence=0.4,
                first_seen=old_time,
                last_seen=old_time,
                source_msg_ids=[10],
            )
        }
        # 표기는 살짝 달라도(공백/구두점 차이) 정규형이 같으면 같은 topic.
        new_obs = [
            InsightMeta(
                topic="맥북에어 가격!",
                text="맥북에어 가격을 다시 조회함",
                source_msg_ids=[42],
            )
        ]
        merged, changed = merge_insights(existing, new_obs, promotion_threshold=3)

        cur = merged["맥북에어가격"]
        assert cur.evidence_count == 2
        assert cur.text == "맥북에어 가격을 다시 조회함"  # 최신 표현으로 갱신
        # source_msg_ids 누적 (중복 없이).
        assert cur.source_msg_ids == [10, 42]
        assert cur.last_seen > old_time
        # first_seen 은 보존.
        assert cur.first_seen == old_time
        # 2/3 보간 → 0.55.
        assert cur.confidence == pytest.approx(0.55)
        assert len(changed) == 1

    def test_promotion_after_n_observations(self):
        """DoD #2: N회(=promotion_threshold) 누적 시 승격."""
        existing: dict = {}
        merged: dict = existing
        for _ in range(3):
            merged, _ = merge_insights(
                merged,
                [InsightMeta(topic="ai 트렌드", text="AI 트렌드를 본다")],
                promotion_threshold=3,
            )

        cur = merged["ai트렌드"]
        assert cur.evidence_count == 3
        assert cur.confidence == 0.7
        assert is_promoted(cur, promotion_threshold=3)

    def test_unrelated_existing_left_untouched(self):
        """이번 회차에 reinforcement 없는 기존 인사이트는 건드리지 않는다."""
        old_time = datetime(2026, 4, 28, 10, 0, 0)
        existing = {
            "정치뉴스": InsightMeta(
                topic="정치뉴스",
                text="정치 뉴스",
                evidence_count=1,
                confidence=0.4,
                first_seen=old_time,
                last_seen=old_time,
            )
        }
        new_obs = [InsightMeta(topic="ai트렌드", text="AI 트렌드")]
        merged, changed = merge_insights(existing, new_obs, promotion_threshold=3)

        assert "정치뉴스" in merged
        assert merged["정치뉴스"].evidence_count == 1
        assert merged["정치뉴스"].last_seen == old_time
        # changed 는 새 토픽만.
        assert [m.topic for m in changed] == ["ai트렌드"]

    def test_empty_topic_in_observations_skipped(self):
        existing: dict = {}
        new_obs = [
            InsightMeta(topic="", text="topic 없음 — skip"),
            InsightMeta(topic="   ", text="공백뿐 — skip"),
            InsightMeta(topic="!!", text="구두점뿐 — skip"),
            InsightMeta(topic="유효", text="유효한 토픽"),
        ]
        merged, changed = merge_insights(existing, new_obs, promotion_threshold=3)
        assert list(merged.keys()) == ["유효"]
        assert len(changed) == 1


# ----------------------------------------------------------------------
# 마이그레이션 스크립트 동작 (USER.md → insights.jsonl)
# ----------------------------------------------------------------------

class TestMigrationScript:
    def test_parse_user_md_extracts_bullets_per_section(self, tmp_path: Path):
        from scripts.migrate_insights import parse_user_md

        md = (
            "# User Profile\n\n"
            "## Preferences\n"
            "- Primary language: Korean\n\n"
            "## Dreaming Insights (2026-04-28)\n"
            "- 정치 뉴스에 관심을 보임\n"
            "- 맥북에어 가격을 조회함\n\n"
            "## Dreaming Insights (2026-04-29)\n"
            "- 정치 뉴스에 관심을 보임\n"
        )
        bullets = parse_user_md(md)
        # Preferences 섹션 bullet 은 Dreaming Insights 가 아니므로 제외.
        # 4-28: 2개, 4-29: 1개 = 총 3개.
        assert len(bullets) == 3
        assert bullets[0][0] == datetime(2026, 4, 28)
        assert bullets[2][0] == datetime(2026, 4, 29)

    def test_build_insights_aggregates_repeated_topics(self):
        from scripts.migrate_insights import build_insights

        bullets = [
            (datetime(2026, 4, 28), "정치 뉴스에 관심을 보임"),
            (datetime(2026, 4, 29), "정치 뉴스에 관심을 보임"),
            (datetime(2026, 4, 30), "정치 뉴스에 관심을 보임"),
        ]
        insights = build_insights(bullets, promotion_threshold=3)
        assert len(insights) == 1
        meta = list(insights.values())[0]
        # 같은 topic 3회 누적 → 승격선 도달.
        assert meta.evidence_count == 3
        assert meta.confidence == 0.7
        # first_seen / last_seen 이 회차 날짜를 정확히 잡는다.
        assert meta.first_seen == datetime(2026, 4, 28)
        assert meta.last_seen == datetime(2026, 4, 30)

    def test_build_insights_same_day_duplicates_count_once(self):
        """같은 날짜 같은 topic 의 중복 bullet 은 1회로만 가산 (날짜 단위 관측)."""
        from scripts.migrate_insights import build_insights

        bullets = [
            (datetime(2026, 4, 28), "정치 뉴스 관심"),
            (datetime(2026, 4, 28), "정치 뉴스 관심"),
        ]
        insights = build_insights(bullets, promotion_threshold=3)
        meta = list(insights.values())[0]
        assert meta.evidence_count == 1
        assert meta.confidence == 0.4

    def test_full_migration_writes_jsonl(self, tmp_path: Path):
        from scripts.migrate_insights import main as migrate_main

        user_md = tmp_path / "USER.md"
        user_md.write_text(
            "## Dreaming Insights (2026-04-28)\n"
            "- 정치 뉴스에 관심을 보임\n"
            "- 맥북에어 가격을 조회함\n",
            encoding="utf-8",
        )
        out = tmp_path / "insights.jsonl"
        rc = migrate_main([
            "--user-file", str(user_md),
            "--out", str(out),
            "--promotion-threshold", "3",
        ])
        assert rc == 0
        assert out.is_file()
        loaded = InsightStore(out).load()
        # 두 개의 별개 topic 이 추출돼야 함.
        assert len(loaded) == 2
        for meta in loaded.values():
            # 마이그레이션 시점은 모두 단발 관측 → 0.4 캡.
            assert meta.evidence_count == 1
            assert meta.confidence == 0.4


# ----------------------------------------------------------------------
# BIZ-77 — start_msg_id / end_msg_id 매핑 (DoD #1, #4, #5)
# ----------------------------------------------------------------------

class TestSourceIdRange:
    def test_recompute_id_range_from_source_msg_ids(self):
        """``source_msg_ids`` 의 min/max 가 그대로 ``start_msg_id`` / ``end_msg_id`` 가 된다."""
        meta = InsightMeta(
            topic="t", text="x", source_msg_ids=[42, 7, 19, 100, 3]
        )
        meta.recompute_id_range()
        assert meta.start_msg_id == 3
        assert meta.end_msg_id == 100

    def test_recompute_id_range_empty(self):
        meta = InsightMeta(topic="t", text="x")
        meta.recompute_id_range()
        assert meta.start_msg_id is None
        assert meta.end_msg_id is None

    def test_merge_assigns_id_range_for_new_topic(self):
        """신규 인사이트는 관측 시 message id 범위가 함께 부착된다 (DoD #1 매핑 저장)."""
        merged, changed = merge_insights(
            existing={},
            new_observations=[
                InsightMeta(
                    topic="새주제",
                    text="첫 관측",
                    source_msg_ids=[10, 11, 13],
                )
            ],
            promotion_threshold=3,
        )
        meta = merged["새주제"]
        assert meta.start_msg_id == 10
        assert meta.end_msg_id == 13
        assert changed[0].start_msg_id == 10
        assert changed[0].end_msg_id == 13

    def test_merge_extends_id_range_on_reinforcement(self):
        """재관측 시 새 message id 가 더 크면 end 가, 더 작으면 start 가 갱신된다."""
        existing = {
            "주제": InsightMeta(
                topic="주제",
                text="기존",
                evidence_count=1,
                source_msg_ids=[20, 25],
                start_msg_id=20,
                end_msg_id=25,
            )
        }
        # 새 관측이 기존 범위 양쪽으로 확장되는 케이스.
        merged, _ = merge_insights(
            existing,
            [InsightMeta(topic="주제", text="다시", source_msg_ids=[5, 30])],
            promotion_threshold=3,
        )
        meta = merged["주제"]
        # 누적 후: [20, 25, 5, 30] → min/max
        assert meta.start_msg_id == 5
        assert meta.end_msg_id == 30
        # source_msg_ids 자체도 누적되었는지 다시 한 번 확인 (BIZ-73 회귀).
        assert sorted(meta.source_msg_ids) == [5, 20, 25, 30]

    def test_serialization_roundtrip_preserves_id_range(self, tmp_path: Path):
        path = tmp_path / "insights.jsonl"
        store = InsightStore(path)
        meta = InsightMeta(
            topic="x",
            text="y",
            source_msg_ids=[1, 2, 3],
            start_msg_id=1,
            end_msg_id=3,
        )
        store.save_all({normalize_topic(meta.topic): meta})

        loaded = store.load()["x"]
        assert loaded.start_msg_id == 1
        assert loaded.end_msg_id == 3

    def test_legacy_sidecar_without_id_range_is_backfilled(self, tmp_path: Path):
        """DoD #5: BIZ-73 시점의 sidecar (start/end 필드 없음) 도 그대로 읽힌다.

        파일에 명시 필드가 없어도 ``source_msg_ids`` 로부터 자동 보강된다 —
        마이그레이션 없이 바로 BIZ-77 endpoints 가 동작하도록.
        """
        path = tmp_path / "insights.jsonl"
        path.write_text(
            json.dumps(
                {
                    "topic": "구버전",
                    "text": "구 sidecar",
                    "evidence_count": 2,
                    "confidence": 0.55,
                    "first_seen": "2026-04-28T10:00:00",
                    "last_seen": "2026-04-29T10:00:00",
                    "source_msg_ids": [7, 19, 42],
                    # start_msg_id / end_msg_id 키 자체가 없음.
                }
            )
            + "\n",
            encoding="utf-8",
        )
        loaded = InsightStore(path).load()["구버전"]
        # source_msg_ids 의 min/max 가 자동 보강되어야 한다.
        assert loaded.start_msg_id == 7
        assert loaded.end_msg_id == 42

    def test_find_by_topic_normalizes(self, tmp_path: Path):
        """원문 / 정규형 어느 형태로 조회해도 같은 행을 반환."""
        store = InsightStore(tmp_path / "insights.jsonl")
        meta = InsightMeta(
            topic="맥북에어 가격",
            text="조회",
            source_msg_ids=[1],
        )
        meta.recompute_id_range()
        store.save_all({normalize_topic(meta.topic): meta})

        # 정규형 키.
        got1 = store.find_by_topic("맥북에어가격")
        # 원문 (공백/구두점 차이) — 같은 행.
        got2 = store.find_by_topic("  맥북에어 가격! ")
        assert got1 is not None
        assert got2 is not None
        assert got1.text == got2.text == "조회"

    def test_find_by_topic_missing_returns_none(self, tmp_path: Path):
        store = InsightStore(tmp_path / "insights.jsonl")
        assert store.find_by_topic("없음") is None
        assert store.find_by_topic("") is None
