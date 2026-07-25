"""Agent Study Wiki 패키지.

SimpleClaw 가 사용자의 관심사·Dreaming 결과·중요 뉴스를 매일 "공부"해서
Markdown 위키로 축적하고, 질문이 들어올 때 맥락으로 끌어다 쓰기 위한 저장소다.

설계 결정 — Markdown 이 source of truth:
    초기 MVP 의 사람이 보는 source of truth 는 Markdown page 다. DB/임베딩은
    어디까지나 검색 인덱스 용도이며, 운영자(형님)가 직접 열어 읽고 수정할 수
    있어야 한다. 따라서 본 패키지는 다음 단순 계층만 제공한다.

    - :mod:`~simpleclaw.study.types` — 핵심 dataclass(주제/페이지/출처).
    - :mod:`~simpleclaw.study.paths` — 위키 루트(``topics.yaml``/``daily``/
      ``topics``) 경로 규약과 초기화.
    - :mod:`~simpleclaw.study.markdown` — ``StudyPage`` ↔ Markdown 직렬화.
    - :mod:`~simpleclaw.study.topic_registry` — ``topics.yaml`` ↔ ``StudyTopic``.

source 계획/수집·관심사 추출·갱신·실행 계층:

    - :mod:`~simpleclaw.study.collectors` — 외부 도구(뉴스/검색/주식 스킬)를 감싸는
      collector 추상화와 fetch 요청/결과 데이터 모델. 실제 도구 호출은 후속 issue
      에서 주입되며, 본 단계는 mockable 한 인터페이스만 고정한다.
    - :mod:`~simpleclaw.study.source_planner` — topic 으로부터 collector 별 fetch
      요청을 생성하고, 일반 뉴스 후보를 relevance score 로 걸러 wiki 에 쓸 가치가
      있는 것만 남긴다.
    - :mod:`~simpleclaw.study.interest_signals` — Dreaming 산출물/대화/사용자
      질문에서 관심사 signal 을 추출해 topic 의 seed 를 만든다. 자동 산출물은 낮은
      가중치로만 반영한다.
    - :mod:`~simpleclaw.study.wiki_updater` — 수집 결과를 topic 페이지(Markdown)에
      *부분 병합*한다. 관리 섹션만 갱신하고 수동 섹션은 보존하며, 저신뢰 항목은
      "확인 필요"로 격리한다.
    - :mod:`~simpleclaw.study.runner` — topic registry → source planner → updater →
      index store 흐름을 하루 1회 실행하는 daily study runner. 데일리 노트도 함께
      남긴다.

설계 결정 — ``StudyTopic`` 두 갈래:
    패키지 레벨 ``StudyTopic`` 은 ``topics.yaml`` 항목을 표현하는 구체 dataclass
    (:mod:`~simpleclaw.study.types`)다. :mod:`~simpleclaw.study.source_planner` 는
    선행 레지스트리를 직접 import 하지 않고 duck typing 하기 위해 동명의 Protocol 을
    내부에 따로 두는데, 이름 충돌을 피하려고 패키지 레벨에서는 노출하지 않는다.
    source planner 인터페이스가 필요하면 ``from simpleclaw.study.source_planner
    import StudyTopic`` 로 명시적으로 가져온다.
"""

from __future__ import annotations

from .collector_adapters import (
    CallbackWebSearchCollector,
    GoogleNewsRSSCollector,
)
from .collectors import (
    CollectorRegistry,
    PlaceholderCollector,
    StudyCollector,
    StudyFetchRequest,
    StudyFetchResult,
)
from .evolution import (
    EvolutionSummary,
    apply_interest_signals,
    interest_signal_to_topic_signal,
    merge_registry_into_raw_topics,
    registry_from_study_topics,
    registry_to_study_topics,
    slugify_topic_id,
)
from .index_store import StudyIndexStore
from .interest_signals import (
    AUTO_REPORT_MAX_WEIGHT,
    INSIGHT_MIN_CONFIDENCE,
    MEMORY_ITEM_WEIGHTS,
    USER_MESSAGE_BASE_WEIGHT,
    InterestSignal,
    derive_topic_hint,
    extract_keywords,
    extract_topic_hints,
    signals_from_auto_reports,
    signals_from_insights,
    signals_from_memory_items,
    signals_from_user_messages,
)
from .markdown import parse_study_page, render_study_page
from .paths import (
    daily_dir,
    index_path,
    init_wiki_root,
    topic_page_path,
    topics_dir,
    topics_yaml_path,
    wiki_root,
)
from .retriever import (
    StudyRetrievalConfig,
    StudyRetriever,
    StudyTopicMatch,
)
from .runner import (
    StudyRunner,
    StudyRunSummary,
    StudyTopicRecord,
    load_daily_digest_prompt,
    load_topic_update_prompt,
)
from .scorer import (
    DEFAULT_SCORE_WEIGHTS,
    ScoreWeights,
    compute_topic_score,
    normalize_mentions,
    recency_decay_factor,
)
from .signal_provider import (
    SourceBackedSignalProvider,
    StaticStudySignalProvider,
    StudySignalProvider,
    build_interest_signals_from_sources,
    merge_signal_providers,
)
from .source_planner import (
    DEFAULT_RELEVANCE_THRESHOLD,
    DEFAULT_SOURCE_POLICY,
    CategorySourcePolicy,
    ConfidenceRelevanceScorer,
    RelevanceAssessment,
    RelevanceScorer,
    SourcePolicy,
    TopicKind,
    WikiSelection,
    load_source_policy,
    plan_fetch_requests,
    select_wiki_worthy,
)
from .topic_registry import (
    EvolvingTopicRegistry,
    SignalSource,
    Topic,
    TopicEvolutionPolicy,
    TopicRegistry,
    TopicSignal,
    TopicState,
    load_topics,
    save_topics,
)
from .types import (
    StudyItemRecord,
    StudyItemStatus,
    StudyPage,
    StudySource,
    StudyTopic,
)
from .wiki_updater import (
    CANONICAL_SECTION_ORDER,
    MANAGED_SECTIONS,
    merge_open_questions,
    merge_study_update,
)

__all__ = [
    # interest_signals
    "AUTO_REPORT_MAX_WEIGHT",
    # wiki_updater
    "CANONICAL_SECTION_ORDER",
    # source_planner
    "DEFAULT_RELEVANCE_THRESHOLD",
    # scorer
    "DEFAULT_SCORE_WEIGHTS",
    "DEFAULT_SOURCE_POLICY",
    "INSIGHT_MIN_CONFIDENCE",
    "MANAGED_SECTIONS",
    "MEMORY_ITEM_WEIGHTS",
    "USER_MESSAGE_BASE_WEIGHT",
    # collector_adapters
    "CallbackWebSearchCollector",
    "CategorySourcePolicy",
    # collectors
    "CollectorRegistry",
    "ConfidenceRelevanceScorer",
    # evolution
    "EvolutionSummary",
    # topic registry (진화형 생애주기)
    "EvolvingTopicRegistry",
    "GoogleNewsRSSCollector",
    "InterestSignal",
    "PlaceholderCollector",
    "RelevanceAssessment",
    "RelevanceScorer",
    "ScoreWeights",
    "SignalSource",
    # signal_provider
    "SourceBackedSignalProvider",
    "SourcePolicy",
    "StaticStudySignalProvider",
    "StudyCollector",
    "StudyFetchRequest",
    "StudyFetchResult",
    # index_store
    "StudyIndexStore",
    # types
    "StudyItemRecord",
    "StudyItemStatus",
    "StudyPage",
    # retriever
    "StudyRetrievalConfig",
    "StudyRetriever",
    "StudyRunSummary",
    # runner
    "StudyRunner",
    "StudySignalProvider",
    "StudySource",
    "StudyTopic",
    "StudyTopicMatch",
    "StudyTopicRecord",
    "Topic",
    "TopicEvolutionPolicy",
    "TopicKind",
    # topic registry (영속)
    "TopicRegistry",
    "TopicSignal",
    "TopicState",
    "WikiSelection",
    "apply_interest_signals",
    "build_interest_signals_from_sources",
    "compute_topic_score",
    "daily_dir",
    "derive_topic_hint",
    "extract_keywords",
    "extract_topic_hints",
    "index_path",
    "init_wiki_root",
    "interest_signal_to_topic_signal",
    "load_daily_digest_prompt",
    "load_source_policy",
    "load_topic_update_prompt",
    "load_topics",
    "merge_open_questions",
    "merge_registry_into_raw_topics",
    "merge_signal_providers",
    "merge_study_update",
    "normalize_mentions",
    "parse_study_page",
    "plan_fetch_requests",
    "recency_decay_factor",
    "registry_from_study_topics",
    "registry_to_study_topics",
    # markdown
    "render_study_page",
    "save_topics",
    "select_wiki_worthy",
    "signals_from_auto_reports",
    "signals_from_insights",
    "signals_from_memory_items",
    "signals_from_user_messages",
    "slugify_topic_id",
    "topic_page_path",
    "topics_dir",
    "topics_yaml_path",
    # paths
    "wiki_root",
]
