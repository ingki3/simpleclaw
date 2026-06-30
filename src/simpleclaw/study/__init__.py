"""Agent Study Wiki 패키지.

SimpleClaw 가 사용자의 관심사·Dreaming 결과·중요 뉴스를 매일 "공부"해서
Markdown 위키로 축적하고, 질문이 들어올 때 맥락으로 끌어다 쓰기 위한 저장소다.

설계 결정 — Markdown 이 source of truth:
    초기 MVP 의 사람이 보는 source of truth 는 Markdown page 다. DB/임베딩은
    어디까지나 검색 인덱스 용도이며, 운영자(형님)가 직접 열어 읽고 수정할 수
    있어야 한다. 따라서 본 패키지는 다음 단순 계층만 제공한다.

    - :mod:`~simpleclaw.study.types` — 핵심 dataclass(주제/페이지/출처).
    - :mod:`~simpleclaw.study.paths` — 위키 루트(``topics.yaml``/``daily/``/
      ``topics/``) 경로 규약과 초기화.
    - :mod:`~simpleclaw.study.markdown` — ``StudyPage`` ↔ Markdown 직렬화.
    - :mod:`~simpleclaw.study.topic_registry` — ``topics.yaml`` ↔ ``StudyTopic``.

source 계획/수집 계층(매일 학습할 topic 의 source 를 계획·수집·relevance 필터):

    - :mod:`~simpleclaw.study.collectors` — 외부 도구(뉴스/검색/주식 스킬)를 감싸는
      collector 추상화와 fetch 요청/결과 데이터 모델. 실제 도구 호출은 후속 issue
      에서 주입되며, 본 단계는 mockable 한 인터페이스만 고정한다.
    - :mod:`~simpleclaw.study.source_planner` — topic 으로부터 collector 별 fetch
      요청을 생성하고, 일반 뉴스 후보를 relevance score 로 걸러 wiki 에 쓸 가치가
      있는 것만 남긴다.

설계 결정 — ``StudyTopic`` 두 갈래:
    패키지 레벨 ``StudyTopic`` 은 ``topics.yaml`` 항목을 표현하는 구체 dataclass
    (:mod:`~simpleclaw.study.types`)다. :mod:`~simpleclaw.study.source_planner` 는
    선행 레지스트리를 직접 import 하지 않고 duck typing 하기 위해 동명의 Protocol 을
    내부에 따로 두는데, 이름 충돌을 피하려고 패키지 레벨에서는 노출하지 않는다.
    source planner 인터페이스가 필요하면 ``from simpleclaw.study.source_planner
    import StudyTopic`` 로 명시적으로 가져온다.

    DB·임베딩·일일 스터디 파이프라인 같은 상위 기능은 후속 이슈에서 이 계층을
    얹는다.
"""

from __future__ import annotations

from .collectors import (
    CollectorRegistry,
    PlaceholderCollector,
    StudyCollector,
    StudyFetchRequest,
    StudyFetchResult,
)
from .index_store import StudyIndexStore
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
from .retriever import (
    StudyRetrievalConfig,
    StudyRetriever,
    StudyTopicMatch,
)
from .topic_registry import TopicRegistry, load_topics, save_topics
from .types import (
    StudyItemRecord,
    StudyItemStatus,
    StudyPage,
    StudySource,
    StudyTopic,
)

__all__ = [
    # types
    "StudyItemRecord",
    "StudyItemStatus",
    "StudySource",
    "StudyTopic",
    "StudyPage",
    # paths
    "wiki_root",
    "topics_yaml_path",
    "daily_dir",
    "topics_dir",
    "topic_page_path",
    "index_path",
    "init_wiki_root",
    # index_store
    "StudyIndexStore",
    # markdown
    "render_study_page",
    "parse_study_page",
    # topic registry
    "TopicRegistry",
    "load_topics",
    "save_topics",
    # collectors
    "CollectorRegistry",
    "PlaceholderCollector",
    "StudyCollector",
    "StudyFetchRequest",
    "StudyFetchResult",
    # source_planner
    "DEFAULT_RELEVANCE_THRESHOLD",
    "DEFAULT_SOURCE_POLICY",
    "CategorySourcePolicy",
    "ConfidenceRelevanceScorer",
    "RelevanceAssessment",
    "RelevanceScorer",
    "SourcePolicy",
    "TopicKind",
    "WikiSelection",
    "load_source_policy",
    "plan_fetch_requests",
    "select_wiki_worthy",
    # retriever
    "StudyRetrievalConfig",
    "StudyRetriever",
    "StudyTopicMatch",
]
