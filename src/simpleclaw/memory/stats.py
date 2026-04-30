"""메모리 분포 통계 및 RAG 회상 토큰 절감 측정 모듈 (BIZ-29).

목적:
- ``ConversationStore``의 임베딩 커버리지·클러스터 분포·임베딩 차원 일관성을 한 번에 요약한다.
- ``StructuredLogger``가 적재한 ``rag_retrieve`` 액션 로그를 일자별로 집계하여
  RAG 회상 빈도와 회수된 토큰 합을 추적할 수 있게 한다.

설계 결정:
- 분포 통계는 ``ConversationStore`` 헬퍼만 호출한다(SQL은 store에 캡슐화).
- 모든 결과는 ``to_dict()``를 제공하여 JSON 직렬화·대시보드 노출에 즉시 사용한다.
- RAG 로그 분석은 ``execution_YYYYMMDD.log`` 일별 파일을 순회하며 ``action_type=="rag_retrieve"``
  엔트리만 추출한다. 다른 종류의 로그가 섞여 있어도 영향을 주지 않는다.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from simpleclaw.memory.conversation_store import ConversationStore

logger = logging.getLogger(__name__)

# StructuredLogger에 적재되는 RAG 회상 로그의 action_type.
# orchestrator._retrieve_relevant_context()와 동기화되어야 한다.
RAG_ACTION_TYPE = "rag_retrieve"


@dataclass
class ClusterDistribution:
    """단일 클러스터의 분포 정보."""

    cluster_id: int
    label: str
    actual_member_count: int  # messages.cluster_id 기준 실측치
    stored_member_count: int  # semantic_clusters.member_count 캐시값

    @property
    def drift(self) -> int:
        """캐시와 실측의 차이 — 0이 아니면 드리프트 발생."""
        return self.stored_member_count - self.actual_member_count

    def to_dict(self) -> dict:
        return {
            "id": self.cluster_id,
            "label": self.label,
            "actual_members": self.actual_member_count,
            "stored_members": self.stored_member_count,
            "drift": self.drift,
        }


@dataclass
class MemoryDistributionStats:
    """메모리 인덱스 분포 스냅샷.

    ``compute_distribution_stats()``의 반환 형식. 대시보드와 CLI에서 동일한 dict로 직렬화한다.
    """

    total_messages: int
    messages_with_embedding: int
    coverage_percent: float
    cluster_count: int
    clustered_messages: int
    unclustered_with_embedding: int
    members_min: int
    members_max: int
    members_mean: float
    members_median: float
    embedding_dimensions: dict[int, int]
    cluster_distributions: list[ClusterDistribution]

    @property
    def has_dimension_inconsistency(self) -> bool:
        """임베딩 차원이 2개 이상 섞여 있는지 — 모델 교체 흔적 탐지."""
        return len(self.embedding_dimensions) > 1

    def to_dict(self) -> dict:
        return {
            "total_messages": self.total_messages,
            "messages_with_embedding": self.messages_with_embedding,
            "coverage_percent": round(self.coverage_percent, 2),
            "cluster_count": self.cluster_count,
            "clustered_messages": self.clustered_messages,
            "unclustered_with_embedding": self.unclustered_with_embedding,
            "members": {
                "min": self.members_min,
                "max": self.members_max,
                "mean": round(self.members_mean, 2),
                "median": self.members_median,
            },
            "embedding_dimensions": {
                str(dim): cnt for dim, cnt in self.embedding_dimensions.items()
            },
            "has_dimension_inconsistency": self.has_dimension_inconsistency,
            "clusters": [c.to_dict() for c in self.cluster_distributions],
        }


def compute_distribution_stats(store: ConversationStore) -> MemoryDistributionStats:
    """``ConversationStore``의 임베딩·클러스터 분포 스냅샷을 계산한다.

    Args:
        store: 대상 ``ConversationStore`` 인스턴스.

    Returns:
        ``MemoryDistributionStats`` 데이터클래스.
    """
    total = store.count()
    with_emb = store.count_with_embedding()
    clustered = store.count_clustered()
    unclustered_emb = store.count_unclustered_with_embedding()
    dim_dist = store.embedding_dimension_distribution()
    actual_counts = store.cluster_member_counts()
    cluster_records = store.list_clusters()

    distributions: list[ClusterDistribution] = []
    for record in cluster_records:
        distributions.append(
            ClusterDistribution(
                cluster_id=record.id,
                label=record.label,
                actual_member_count=actual_counts.get(record.id, 0),
                stored_member_count=record.member_count,
            )
        )

    member_values = [d.actual_member_count for d in distributions]
    if member_values:
        m_min = min(member_values)
        m_max = max(member_values)
        m_mean = sum(member_values) / len(member_values)
        m_median = float(statistics.median(member_values))
    else:
        m_min = 0
        m_max = 0
        m_mean = 0.0
        m_median = 0.0

    coverage = (with_emb / total * 100.0) if total > 0 else 0.0

    return MemoryDistributionStats(
        total_messages=total,
        messages_with_embedding=with_emb,
        coverage_percent=coverage,
        cluster_count=len(distributions),
        clustered_messages=clustered,
        unclustered_with_embedding=unclustered_emb,
        members_min=m_min,
        members_max=m_max,
        members_mean=m_mean,
        members_median=m_median,
        embedding_dimensions=dim_dist,
        cluster_distributions=distributions,
    )


# ------------------------------------------------------------------
# RAG 회상 로그 분석 — 토큰 절감 추세
# ------------------------------------------------------------------


@dataclass
class RagDailySummary:
    """단일 일자(YYYYMMDD)의 RAG 회상 집계."""

    date: str
    total_calls: int
    hits: int
    misses: int
    recalled_messages_sum: int
    recalled_tokens_sum: int
    avg_recalled_tokens: float

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total_calls if self.total_calls > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "total_calls": self.total_calls,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "recalled_messages_sum": self.recalled_messages_sum,
            "recalled_tokens_sum": self.recalled_tokens_sum,
            "avg_recalled_tokens": round(self.avg_recalled_tokens, 2),
        }


@dataclass
class RagAnalysisResult:
    """기간 단위 RAG 회상 집계 결과."""

    days: int
    daily: list[RagDailySummary]
    total_calls: int
    total_hits: int
    total_recalled_tokens: int
    hit_rate: float
    avg_recalled_tokens: float

    def to_dict(self) -> dict:
        return {
            "days": self.days,
            "total_calls": self.total_calls,
            "total_hits": self.total_hits,
            "total_recalled_tokens": self.total_recalled_tokens,
            "hit_rate": round(self.hit_rate, 4),
            "avg_recalled_tokens": round(self.avg_recalled_tokens, 2),
            "daily": [d.to_dict() for d in self.daily],
        }


def analyze_rag_logs(
    log_dir: str | Path,
    days: int = 7,
    *,
    today: datetime | None = None,
) -> RagAnalysisResult:
    """최근 ``days``일치 RAG 회상 로그를 일자별로 집계한다.

    각 ``execution_YYYYMMDD.log`` 파일을 라인 단위 JSON으로 파싱하고
    ``action_type == "rag_retrieve"``인 엔트리만 골라
    ``details.hit`` / ``details.recalled_messages`` / ``details.recalled_tokens``를 누적한다.

    파싱 실패 라인이나 누락 필드는 조용히 건너뛴다(로그 손상 보호).

    Args:
        log_dir: ``StructuredLogger``가 사용한 로그 디렉터리.
        days: 오늘부터 거슬러 올라갈 일수.
        today: 테스트 주입용 기준일. None이면 ``datetime.now()``.

    Returns:
        기간 합계와 일자별 요약을 담은 ``RagAnalysisResult``.
    """
    log_dir_p = Path(log_dir)
    base = today or datetime.now()

    daily: list[RagDailySummary] = []
    total_calls = 0
    total_hits = 0
    total_recalled_tokens = 0

    for offset in range(days):
        target = base - timedelta(days=offset)
        date_str = target.strftime("%Y%m%d")
        log_path = log_dir_p / f"execution_{date_str}.log"
        if not log_path.is_file():
            continue

        calls = 0
        hits = 0
        misses = 0
        recalled_msgs = 0
        recalled_tokens = 0

        try:
            with open(log_path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("action_type") != RAG_ACTION_TYPE:
                        continue
                    details = entry.get("details") or {}
                    calls += 1
                    if details.get("hit"):
                        hits += 1
                    else:
                        misses += 1
                    # 손상된 details 필드는 0으로 fallback — 카운트만이라도 반영
                    try:
                        recalled_msgs += int(details.get("recalled_messages", 0) or 0)
                    except (TypeError, ValueError):
                        pass
                    try:
                        recalled_tokens += int(details.get("recalled_tokens", 0) or 0)
                    except (TypeError, ValueError):
                        pass
        except OSError as exc:
            logger.warning("Cannot read RAG log %s: %s", log_path, exc)
            continue

        avg = (recalled_tokens / calls) if calls > 0 else 0.0
        daily.append(
            RagDailySummary(
                date=date_str,
                total_calls=calls,
                hits=hits,
                misses=misses,
                recalled_messages_sum=recalled_msgs,
                recalled_tokens_sum=recalled_tokens,
                avg_recalled_tokens=avg,
            )
        )
        total_calls += calls
        total_hits += hits
        total_recalled_tokens += recalled_tokens

    # 일자 오름차순 정렬 — 출력 시점에 시간순으로 보기 편하도록
    daily.sort(key=lambda d: d.date)

    hit_rate = (total_hits / total_calls) if total_calls > 0 else 0.0
    avg_tokens = (total_recalled_tokens / total_calls) if total_calls > 0 else 0.0

    return RagAnalysisResult(
        days=days,
        daily=daily,
        total_calls=total_calls,
        total_hits=total_hits,
        total_recalled_tokens=total_recalled_tokens,
        hit_rate=hit_rate,
        avg_recalled_tokens=avg_tokens,
    )
