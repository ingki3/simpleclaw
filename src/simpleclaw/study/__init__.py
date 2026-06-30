"""Agent Study Wiki — 매일 학습할 topic 의 source 계획/수집/relevance 필터 모듈.

이 패키지는 SimpleClaw 가 사용자의 관심사와 일반 뉴스를 매일 "공부"하기 위한
파이프라인의 source 계획 단계를 담당한다.

- :mod:`collectors` — 외부 도구(뉴스/검색/주식 스킬)를 감싸는 collector 추상화와
  fetch 요청/결과 데이터 모델. 실제 도구 호출은 후속 issue 에서 주입되며, 본 단계는
  mockable 한 인터페이스만 고정한다.
- :mod:`source_planner` — topic 으로부터 collector 별 fetch 요청을 생성하고, 일반
  뉴스 후보를 relevance score 로 걸러 wiki 에 쓸 가치가 있는 것만 남긴다.
"""

from __future__ import annotations

from simpleclaw.study.collectors import (
    CollectorRegistry,
    PlaceholderCollector,
    StudyCollector,
    StudyFetchRequest,
    StudyFetchResult,
)
from simpleclaw.study.scorer import (
    DEFAULT_SCORE_WEIGHTS,
    ScoreWeights,
    compute_topic_score,
    normalize_mentions,
    recency_decay_factor,
)
from simpleclaw.study.source_planner import (
    DEFAULT_RELEVANCE_THRESHOLD,
    DEFAULT_SOURCE_POLICY,
    CategorySourcePolicy,
    ConfidenceRelevanceScorer,
    RelevanceAssessment,
    RelevanceScorer,
    SourcePolicy,
    StudyTopic,
    TopicKind,
    WikiSelection,
    load_source_policy,
    plan_fetch_requests,
    select_wiki_worthy,
)
from simpleclaw.study.topic_registry import (
    InterestSignal,
    SignalSource,
    Topic,
    TopicEvolutionPolicy,
    TopicRegistry,
    TopicState,
)

__all__ = [
    # collectors
    "CollectorRegistry",
    "PlaceholderCollector",
    "StudyCollector",
    "StudyFetchRequest",
    "StudyFetchResult",
    # scorer
    "DEFAULT_SCORE_WEIGHTS",
    "ScoreWeights",
    "compute_topic_score",
    "normalize_mentions",
    "recency_decay_factor",
    # source_planner
    "DEFAULT_RELEVANCE_THRESHOLD",
    "DEFAULT_SOURCE_POLICY",
    "CategorySourcePolicy",
    "ConfidenceRelevanceScorer",
    "RelevanceAssessment",
    "RelevanceScorer",
    "SourcePolicy",
    "StudyTopic",
    "TopicKind",
    "WikiSelection",
    "load_source_policy",
    "plan_fetch_requests",
    "select_wiki_worthy",
    # topic_registry
    "InterestSignal",
    "SignalSource",
    "Topic",
    "TopicEvolutionPolicy",
    "TopicRegistry",
    "TopicState",
]
