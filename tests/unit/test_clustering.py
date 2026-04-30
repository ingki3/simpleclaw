"""IncrementalClusterer 단위 테스트 (spec 005 Phase 3).

검증 범위:
- find_nearest: 임계값 이상이면 부착, 미만이면 None 반환
- find_nearest: 차원 불일치/0 norm centroid 자동 제외
- update_centroid: 누적 평균 공식, 빈 카운트(=새 클러스터) 처리
- 잘못된 입력(0 벡터, 빈 벡터, 다차원)에 대한 ValueError
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from simpleclaw.memory.clustering import (
    ClusterAssignment,
    IncrementalClusterer,
)
from simpleclaw.memory.models import ClusterRecord


def _cluster(cid: int, centroid: list[float], member_count: int = 1) -> ClusterRecord:
    return ClusterRecord(
        id=cid,
        label=f"c{cid}",
        centroid=np.array(centroid, dtype=np.float32),
        summary="",
        member_count=member_count,
        updated_at=datetime.now(),
    )


class TestFindNearest:
    def test_attach_when_above_threshold(self):
        clusterer = IncrementalClusterer(threshold=0.75)
        clusters = [_cluster(1, [1.0, 0.0]), _cluster(2, [0.0, 1.0])]

        assignment = clusterer.find_nearest(np.array([1.0, 0.1]), clusters)
        assert assignment.cluster_id == 1
        assert assignment.score > 0.9

    def test_returns_none_when_below_threshold(self):
        clusterer = IncrementalClusterer(threshold=0.95)
        clusters = [_cluster(1, [1.0, 0.0])]

        # cosine ≈ 0.707 < 0.95 임계
        assignment = clusterer.find_nearest(np.array([1.0, 1.0]), clusters)
        assert assignment.cluster_id is None
        assert 0.6 < assignment.score < 0.8

    def test_empty_clusters_returns_none(self):
        clusterer = IncrementalClusterer()
        assignment = clusterer.find_nearest(np.array([1.0, 0.0]), [])
        assert assignment.cluster_id is None
        assert assignment.score == -1.0

    def test_picks_highest_match(self):
        clusterer = IncrementalClusterer(threshold=0.0)
        clusters = [
            _cluster(1, [1.0, 0.0]),
            _cluster(2, [0.7, 0.7]),
            _cluster(3, [0.0, 1.0]),
        ]
        # query는 c2와 거의 동일 — 가장 높은 매칭을 선택해야 함
        assignment = clusterer.find_nearest(np.array([0.7, 0.7]), clusters)
        assert assignment.cluster_id == 2

    def test_skips_dimension_mismatch(self):
        clusterer = IncrementalClusterer(threshold=0.0)
        clusters = [
            _cluster(1, [1.0, 0.0, 0.0]),  # 3D — query와 차원 불일치
            _cluster(2, [1.0, 0.0]),  # 2D — 일치
        ]
        assignment = clusterer.find_nearest(np.array([1.0, 0.0]), clusters)
        assert assignment.cluster_id == 2

    def test_skips_zero_norm_centroid(self):
        clusterer = IncrementalClusterer(threshold=0.0)
        clusters = [_cluster(1, [0.0, 0.0]), _cluster(2, [1.0, 0.0])]
        assignment = clusterer.find_nearest(np.array([1.0, 0.0]), clusters)
        assert assignment.cluster_id == 2

    def test_zero_query_raises(self):
        clusterer = IncrementalClusterer()
        with pytest.raises(ValueError, match="zero vector"):
            clusterer.find_nearest(np.zeros(3), [_cluster(1, [1.0, 0.0, 0.0])])

    def test_empty_query_raises(self):
        clusterer = IncrementalClusterer()
        with pytest.raises(ValueError, match="non-empty"):
            clusterer.find_nearest(np.array([]), [])

    def test_multidim_query_raises(self):
        clusterer = IncrementalClusterer()
        with pytest.raises(ValueError, match="1-D"):
            clusterer.find_nearest(np.zeros((2, 2)), [])


class TestUpdateCentroid:
    def test_first_member_returns_vector(self):
        clusterer = IncrementalClusterer()
        new = clusterer.update_centroid(
            np.zeros(3, dtype=np.float32), 0, np.array([1.0, 2.0, 3.0])
        )
        np.testing.assert_allclose(new, [1.0, 2.0, 3.0])

    def test_incremental_mean(self):
        clusterer = IncrementalClusterer()
        # old=[1,0], n=1 → 새 [3,0] 추가 시 평균은 [2,0]
        new = clusterer.update_centroid(
            np.array([1.0, 0.0]), 1, np.array([3.0, 0.0])
        )
        np.testing.assert_allclose(new, [2.0, 0.0])

    def test_dimension_mismatch_raises(self):
        clusterer = IncrementalClusterer()
        with pytest.raises(ValueError, match="dimension mismatch"):
            clusterer.update_centroid(
                np.array([1.0, 0.0]), 1, np.array([1.0, 0.0, 0.0])
            )

    def test_negative_count_raises(self):
        clusterer = IncrementalClusterer()
        with pytest.raises(ValueError, match="non-negative"):
            clusterer.update_centroid(
                np.array([1.0]), -1, np.array([1.0])
            )

    def test_returns_float32(self):
        clusterer = IncrementalClusterer()
        new = clusterer.update_centroid(
            np.array([1.0, 2.0]), 1, np.array([3.0, 4.0])
        )
        assert new.dtype == np.float32


class TestClustererInit:
    def test_threshold_out_of_range_raises(self):
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            IncrementalClusterer(threshold=1.5)

    def test_default_threshold(self):
        clusterer = IncrementalClusterer()
        assert clusterer.threshold == 0.75


class TestAssignmentDataclass:
    def test_assignment_holds_score(self):
        a = ClusterAssignment(cluster_id=42, score=0.9)
        assert a.cluster_id == 42
        assert a.score == 0.9
