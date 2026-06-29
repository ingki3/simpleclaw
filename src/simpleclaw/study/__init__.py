"""Agent Study Wiki — 매일 학습할 topic 의 source 계획/수집/relevance 필터 모듈.

이 패키지는 SimpleClaw 가 사용자의 관심사와 일반 뉴스를 매일 "공부"하기 위한
파이프라인의 source 계획 단계를 담당한다.

- :mod:`collectors` — 외부 도구(뉴스/검색/주식 스킬)를 감싸는 collector 추상화와
  fetch 요청/결과 데이터 모델. 실제 도구 호출은 후속 issue 에서 주입되며, 본 단계는
  mockable 한 인터페이스만 고정한다.
- :mod:`source_planner` — topic 으로부터 collector 별 fetch 요청을 생성하고, 일반
  뉴스 후보를 relevance score 로 걸러 wiki 에 쓸 가치가 있는 것만 남긴다.
- :mod:`wiki_updater` — 수집 결과를 topic 페이지(Markdown)에 *부분 병합*한다. 관리
  섹션만 갱신하고 수동 섹션은 보존하며, 저신뢰 항목은 "확인 필요"로 격리한다.
- :mod:`runner` — topic registry → source planner → updater → index store 흐름을
  하루 1회 실행하는 daily study runner. 데일리 노트도 함께 남긴다.
"""

from __future__ import annotations

from simpleclaw.study.collectors import (
    CollectorRegistry,
    PlaceholderCollector,
    StudyCollector,
    StudyFetchRequest,
    StudyFetchResult,
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
from simpleclaw.study.runner import (
    StudyRunner,
    StudyRunSummary,
    StudyTopicRecord,
    load_daily_digest_prompt,
    load_topic_update_prompt,
)
from simpleclaw.study.wiki_updater import (
    CANONICAL_SECTION_ORDER,
    MANAGED_SECTIONS,
    merge_open_questions,
    merge_study_update,
)

__all__ = [
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
    "StudyTopic",
    "TopicKind",
    "WikiSelection",
    "load_source_policy",
    "plan_fetch_requests",
    "select_wiki_worthy",
    # wiki_updater
    "CANONICAL_SECTION_ORDER",
    "MANAGED_SECTIONS",
    "merge_open_questions",
    "merge_study_update",
    # runner
    "StudyRunner",
    "StudyRunSummary",
    "StudyTopicRecord",
    "load_daily_digest_prompt",
    "load_topic_update_prompt",
]
