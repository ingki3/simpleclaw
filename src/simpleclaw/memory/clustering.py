"""점진 클러스터링(IncrementalClusterer) — 시맨틱 메모리 그래프 갱신용.

주요 동작 흐름 (spec 005 Phase 3):
1. 드리밍이 미처리 메시지의 임베딩을 하나씩 보낸다.
2. ``find_nearest()``로 기존 클러스터 centroid 중 코사인 유사도가 가장 높은 것을 찾는다.
3. 유사도가 임계값(``threshold``) 이상이면 그 클러스터에 부착, 미만이면 새 클러스터를 생성하라고 알린다.
4. 멤버 추가 시 ``update_centroid()``로 평균 벡터를 incremental update 한다.

설계 결정:
- 알고리즘 = 순수 numpy 코사인 임계값 응집(agglomerative). 추가 의존성 없음.
  메시지 수가 수만 단위로 늘어나도 클러스터 수는 보통 수십~수백이라 O(messages × clusters)는 충분.
- centroid는 단위 정규화하지 않은 평균을 저장한다. 검색 시점에 norm으로 나누어 코사인 계산.
  이렇게 두면 incremental mean 업데이트가 간단(``(old*n + new) / (n+1)``).
- 차원 불일치(레거시 임베딩 vs 현재 모델)는 ``find_nearest`` 단계에서 자동 제외하여
  알고리즘이 크래시 없이 동작하도록 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from simpleclaw.memory.models import ClusterRecord

logger = logging.getLogger(__name__)


@dataclass
class ClusterAssignment:
    """클러스터 할당 결과.

    Attributes:
        cluster_id: 부착할 기존 클러스터 id. ``None``이면 신규 클러스터를 생성해야 함.
        score: 매칭된 클러스터와의 코사인 유사도 [-1, 1].
            ``cluster_id is None``인 경우엔 best-match 점수(임계값 미만)이거나
            기존 클러스터가 전혀 없을 땐 ``-1.0``.
    """
    cluster_id: int | None
    score: float


class IncrementalClusterer:
    """임계값 기반 점진 클러스터링.

    한 번에 하나의 임베딩을 받아 기존 클러스터들과 비교한 뒤,
    부착할 클러스터 id를 결정하거나 신규 클러스터 생성을 권한다.
    실제 DB 쓰기는 호출자(드리밍 파이프라인)가 ``ConversationStore`` API로 수행한다.
    """

    def __init__(self, threshold: float = 0.75) -> None:
        """클러스터 임계값을 설정한다.

        Args:
            threshold: 부착을 허용하는 최소 코사인 유사도. 기본 0.75는
                multilingual-e5-small에서 "같은 주제"라 부를 만한 경험적 컷이다.
                값이 낮을수록 클러스터가 커지고(잡음↑), 높을수록 작아진다(파편화↑).
        """
        if not -1.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [-1, 1]")
        self._threshold = float(threshold)

    @property
    def threshold(self) -> float:
        return self._threshold

    def find_nearest(
        self,
        vector: np.ndarray,
        clusters: list[ClusterRecord],
    ) -> ClusterAssignment:
        """주어진 임베딩에 대해 가장 가까운 기존 클러스터를 찾는다.

        매칭 점수가 임계값 이상이면 그 클러스터 id를, 미만이면 ``None``을
        담은 ``ClusterAssignment``를 반환한다. 호출자는 ``cluster_id is None``일 때
        ``ConversationStore.create_cluster()``를 호출하여 신규 클러스터를 만들면 된다.

        Args:
            vector: 분류 대상 임베딩(1-D float32 권장).
            clusters: 비교 대상 클러스터 목록(``list_clusters()`` 결과).

        Returns:
            ``ClusterAssignment`` — 부착 권장 cluster_id 또는 ``None``과 best-match 점수.
        """
        vec = np.asarray(vector, dtype=np.float32)
        if vec.ndim != 1 or vec.size == 0:
            raise ValueError("vector must be a non-empty 1-D array")

        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            # 0 벡터는 의미 없는 임베딩 — 매칭 불가, 신규 클러스터 생성도 무의미
            raise ValueError("vector must not be a zero vector")
        unit = vec / norm

        best_id: int | None = None
        best_score = -1.0
        for cluster in clusters:
            centroid = cluster.centroid
            if centroid.shape[0] != vec.shape[0]:
                # 차원 다른 레거시 클러스터는 자동 제외(에러 없음)
                continue
            c_norm = float(np.linalg.norm(centroid))
            if c_norm == 0.0:
                continue
            score = float(np.dot(unit, centroid / c_norm))
            if score > best_score:
                best_score = score
                best_id = cluster.id

        if best_id is None or best_score < self._threshold:
            return ClusterAssignment(cluster_id=None, score=best_score)
        return ClusterAssignment(cluster_id=best_id, score=best_score)

    def update_centroid(
        self,
        old_centroid: np.ndarray,
        old_count: int,
        new_vector: np.ndarray,
    ) -> np.ndarray:
        """기존 centroid에 신규 멤버 임베딩을 누적 평균으로 합친다.

        공식: ``(old * n + new) / (n + 1)``. 단위 정규화는 의도적으로 수행하지 않으며
        검색 시점에 norm으로 나누어 코사인을 계산한다.

        Args:
            old_centroid: 기존 평균 벡터.
            old_count: 기존 멤버 수(>= 0).
            new_vector: 새로 추가될 임베딩.

        Returns:
            갱신된 centroid (float32, 1-D).

        Raises:
            ValueError: 차원이 일치하지 않거나 음수 count인 경우.
        """
        old = np.asarray(old_centroid, dtype=np.float32)
        new = np.asarray(new_vector, dtype=np.float32)
        if old.ndim != 1 or new.ndim != 1:
            raise ValueError("centroid and vector must be 1-D")
        if old.shape[0] != new.shape[0]:
            raise ValueError(
                f"dimension mismatch: centroid={old.shape[0]}, vector={new.shape[0]}"
            )
        if old_count < 0:
            raise ValueError("old_count must be non-negative")
        if old_count == 0:
            # 최초 멤버 — 새 임베딩 자체가 centroid
            return new.copy()
        return ((old * old_count + new) / (old_count + 1)).astype(np.float32)
