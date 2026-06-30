"""Study topic scorer 의 가중합·clamp·신호 정규화 헬퍼 검증.

DoD 연계:
- 반복적이고 관심도 높은 주제는 승격 임계값(0.70) 이상의 점수를 받는다.
- 일회성/오래된 신호는 점수가 낮아 cooling/archive 로 흐른다(최근성 감쇠).
"""

from __future__ import annotations

import pytest

from simpleclaw.study.scorer import (
    DEFAULT_SCORE_WEIGHTS,
    ScoreWeights,
    compute_topic_score,
    normalize_mentions,
    recency_decay_factor,
)


class TestComputeTopicScore:
    def test_promotes_repeated_high_interest_topic(self):
        # 이슈 본문의 예시: 반복·고관심 주제는 0.70 이상.
        score = compute_topic_score(
            user_interest=0.9,
            repeated_mentions=0.8,
            freshness_need=0.7,
            global_importance=0.4,
            recency_decay=0.8,
        )
        assert score >= 0.70

    def test_matches_weighted_sum(self):
        score = compute_topic_score(
            user_interest=0.9,
            repeated_mentions=0.8,
            freshness_need=0.7,
            global_importance=0.4,
            recency_decay=0.8,
        )
        expected = (
            0.35 * 0.9 + 0.20 * 0.8 + 0.20 * 0.7 + 0.15 * 0.4 + 0.10 * 0.8
        )
        assert score == pytest.approx(expected)

    def test_all_zero_signals_score_zero(self):
        score = compute_topic_score(
            user_interest=0.0,
            repeated_mentions=0.0,
            freshness_need=0.0,
            global_importance=0.0,
            recency_decay=0.0,
        )
        assert score == 0.0

    def test_all_max_signals_score_one(self):
        score = compute_topic_score(
            user_interest=1.0,
            repeated_mentions=1.0,
            freshness_need=1.0,
            global_importance=1.0,
            recency_decay=1.0,
        )
        assert score == 1.0

    def test_clamps_out_of_range_inputs(self):
        # 범위를 벗어난 입력도 각 신호를 clamp 한 뒤 합산 → 결과는 [0,1].
        score = compute_topic_score(
            user_interest=5.0,
            repeated_mentions=-3.0,
            freshness_need=2.0,
            global_importance=-1.0,
            recency_decay=9.0,
        )
        # clamp 후: user=1, repeated=0, fresh=1, global=0, recency=1
        expected = 0.35 * 1 + 0.20 * 0 + 0.20 * 1 + 0.15 * 0 + 0.10 * 1
        assert score == pytest.approx(expected)
        assert 0.0 <= score <= 1.0

    def test_low_interest_one_shot_stays_below_active_threshold(self):
        # 관심도 낮고 한 번 언급(반복 0)·오래된(recency 낮음) 주제는 0.55 미만.
        score = compute_topic_score(
            user_interest=0.2,
            repeated_mentions=0.1,
            freshness_need=0.3,
            global_importance=0.2,
            recency_decay=0.1,
        )
        assert score < 0.55

    def test_custom_weights_override(self):
        weights = ScoreWeights(
            user_interest=1.0,
            repeated_mentions=0.0,
            freshness_need=0.0,
            global_importance=0.0,
            recency_decay=0.0,
        )
        score = compute_topic_score(
            user_interest=0.6,
            repeated_mentions=1.0,
            freshness_need=1.0,
            global_importance=1.0,
            recency_decay=1.0,
            weights=weights,
        )
        assert score == pytest.approx(0.6)


class TestScoreWeights:
    def test_default_weights_sum_to_one(self):
        w = DEFAULT_SCORE_WEIGHTS
        total = (
            w.user_interest
            + w.repeated_mentions
            + w.freshness_need
            + w.global_importance
            + w.recency_decay
        )
        assert total == pytest.approx(1.0)

    def test_non_normalized_weights_rejected(self):
        with pytest.raises(ValueError):
            ScoreWeights(
                user_interest=0.5,
                repeated_mentions=0.5,
                freshness_need=0.5,
                global_importance=0.0,
                recency_decay=0.0,
            )


class TestNormalizeMentions:
    def test_zero_or_negative_is_zero(self):
        assert normalize_mentions(0) == 0.0
        assert normalize_mentions(-2) == 0.0

    def test_saturation_curve_is_monotonic_and_bounded(self):
        prev = -1.0
        for count in range(1, 30):
            value = normalize_mentions(count)
            assert 0.0 <= value < 1.0
            assert value > prev  # 단조 증가
            prev = value

    def test_half_point_at_saturation(self):
        # count == saturation 이면 정확히 0.5.
        assert normalize_mentions(3, saturation=3.0) == pytest.approx(0.5)

    def test_degenerate_saturation_returns_max(self):
        assert normalize_mentions(1, saturation=0.0) == 1.0


class TestRecencyDecayFactor:
    def test_fresh_signal_is_one(self):
        assert recency_decay_factor(0.0) == 1.0
        assert recency_decay_factor(-5.0) == 1.0  # 미래 타임스탬프 방어

    def test_half_life_halves(self):
        assert recency_decay_factor(168.0, half_life_hours=168.0) == pytest.approx(0.5)
        assert recency_decay_factor(336.0, half_life_hours=168.0) == pytest.approx(0.25)

    def test_monotonic_decreasing(self):
        prev = 1.1
        for age in (0, 24, 72, 168, 336, 720):
            value = recency_decay_factor(float(age))
            assert 0.0 <= value <= 1.0
            assert value < prev
            prev = value

    def test_degenerate_half_life_returns_zero_for_past(self):
        assert recency_decay_factor(10.0, half_life_hours=0.0) == 0.0
