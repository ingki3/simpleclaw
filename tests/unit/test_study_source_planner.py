"""Study source planner 의 fetch 계획, collector 인터페이스, relevance gate 검증.

DoD:
- topic 에서 fetch 요청을 생성한다.
- collector 는 실제 도구 호출 없이 mockable 인터페이스를 제공한다.
- 일반 뉴스 후보는 relevance score 가 낮으면 wiki 에 쓰지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from simpleclaw.study.collectors import (
    CollectorRegistry,
    PlaceholderCollector,
    StudyFetchRequest,
    StudyFetchResult,
)
from simpleclaw.study.source_planner import (
    DEFAULT_SOURCE_POLICY,
    ConfidenceRelevanceScorer,
    RelevanceAssessment,
    TopicKind,
    load_news_relevance_prompt,
    load_source_policy,
    plan_fetch_requests,
    select_wiki_worthy,
)


@dataclass
class FakeTopic:
    """StudyTopic Protocol 을 만족하는 테스트용 topic."""

    topic_id: str
    label: str
    category: str
    kind: TopicKind = TopicKind.USER_INTEREST
    max_sources: int = 3
    freshness_hours: int = 24


def _result(
    topic_id: str,
    *,
    text: str = "본문",
    confidence: float = 0.9,
    limitations: tuple[str, ...] = (),
    collector: str = "news-search-skill",
) -> StudyFetchResult:
    request = StudyFetchRequest(topic_id=topic_id, query="q", collector=collector)
    return StudyFetchResult(
        request=request,
        title="t",
        text=text,
        confidence=confidence,
        limitations=limitations,
    )


# --------------------------------------------------------------------------- #
# plan_fetch_requests — topic 에서 fetch 요청 생성
# --------------------------------------------------------------------------- #


def test_plan_generates_one_request_per_collector_for_category():
    topic = FakeTopic("t1", "AI 반도체", "ai-industry", max_sources=5, freshness_hours=12)

    requests = plan_fetch_requests([topic])

    collectors = [r.collector for r in requests]
    assert collectors == ["news-search-skill", "web_search"]
    assert all(r.topic_id == "t1" for r in requests)
    assert all(r.query == "AI 반도체" for r in requests)
    assert all(r.max_sources == 5 and r.freshness_hours == 12 for r in requests)


def test_plan_uses_fallback_for_unknown_category():
    topic = FakeTopic("t2", "잡담", "unknown-category")

    requests = plan_fetch_requests([topic])

    assert [r.collector for r in requests] == ["news-search-skill", "web_search"]


def test_plan_dedupes_duplicate_collectors_in_policy():
    policy = load_source_policy(
        {"default_sources": {"dup": {"collectors": ["a", "a", "b"]}}}
    )
    topic = FakeTopic("t3", "x", "dup")

    requests = plan_fetch_requests([topic], policy=policy)

    assert [r.collector for r in requests] == ["a", "b"]


def test_plan_preserves_topic_then_collector_order():
    topics = [
        FakeTopic("t1", "AI", "ai-industry"),
        FakeTopic("t2", "증시", "markets"),
    ]

    requests = plan_fetch_requests(topics)

    assert [(r.topic_id, r.collector) for r in requests][:3] == [
        ("t1", "news-search-skill"),
        ("t1", "web_search"),
        ("t2", "us-stock-skill"),
    ]


# --------------------------------------------------------------------------- #
# collector — 실제 도구 호출 없는 mockable 인터페이스
# --------------------------------------------------------------------------- #


def test_placeholder_collector_returns_no_results():
    collector = PlaceholderCollector("news-search-skill")
    request = StudyFetchRequest("t1", "q", "news-search-skill")

    assert collector.fetch(request) == ()


def test_registry_falls_back_to_placeholder_for_unregistered_collector():
    registry = CollectorRegistry()
    request = StudyFetchRequest("t1", "q", "not-registered")

    # 미등록 collector 도 깨지지 않고 no-op 으로 흐른다.
    assert registry.fetch(request) == ()
    assert isinstance(registry.get("not-registered"), PlaceholderCollector)


def test_registry_uses_mock_collector():
    @dataclass
    class StubCollector:
        name: str = "news-search-skill"
        calls: list[StudyFetchRequest] = field(default_factory=list)

        def fetch(self, request: StudyFetchRequest) -> Sequence[StudyFetchResult]:
            self.calls.append(request)
            return [_result(request.topic_id, collector=request.collector)]

    stub = StubCollector()
    registry = CollectorRegistry()
    registry.register(stub)

    requests = plan_fetch_requests([FakeTopic("t1", "AI", "ai-industry")])
    results = registry.fetch_all(requests)

    # news-search-skill 만 stub 으로 등록 → 1건, web_search 는 placeholder no-op.
    assert len(results) == 1
    assert stub.calls[0].collector == "news-search-skill"


# --------------------------------------------------------------------------- #
# select_wiki_worthy — 일반 뉴스 relevance gate
# --------------------------------------------------------------------------- #


def test_user_interest_results_bypass_relevance_gate():
    topic = FakeTopic("t1", "AI", "ai-industry", kind=TopicKind.USER_INTEREST)
    # confidence 가 낮아도 user_interest 면 통과해야 한다.
    results = [_result("t1", confidence=0.1)]

    selection = select_wiki_worthy(results, topics={"t1": topic})

    assert selection.selected == tuple(results)
    assert selection.rejected == ()


def test_low_relevance_general_news_is_rejected():
    topic = FakeTopic("t1", "잡뉴스", "ai-industry", kind=TopicKind.GENERAL_NEWS)
    results = [_result("t1", confidence=0.2)]

    selection = select_wiki_worthy(results, topics={"t1": topic}, threshold=0.5)

    assert selection.selected == ()
    assert len(selection.rejected) == 1
    rejected_result, assessment = selection.rejected[0]
    assert rejected_result is results[0]
    assert assessment.score < 0.5


def test_high_relevance_general_news_is_selected():
    topic = FakeTopic("t1", "중요뉴스", "ai-industry", kind=TopicKind.GENERAL_NEWS)
    results = [_result("t1", confidence=0.9)]

    selection = select_wiki_worthy(results, topics={"t1": topic}, threshold=0.5)

    assert selection.selected == tuple(results)
    assert selection.rejected == ()


def test_unknown_topic_is_treated_as_general_news():
    # topic 매핑에 없는 결과는 보수적으로 general_news gate 를 적용한다.
    results = [_result("ghost", confidence=0.1)]

    selection = select_wiki_worthy(results, topics={})

    assert selection.selected == ()
    assert len(selection.rejected) == 1


def test_custom_scorer_can_override_relevance():
    @dataclass
    class AlwaysHighScorer:
        def score(self, result, *, topic) -> RelevanceAssessment:
            return RelevanceAssessment(score=1.0, should_study=True, reasons=("mock",))

    topic = FakeTopic("t1", "x", "ai-industry", kind=TopicKind.GENERAL_NEWS)
    results = [_result("t1", confidence=0.0)]

    selection = select_wiki_worthy(
        results, topics={"t1": topic}, scorer=AlwaysHighScorer()
    )

    assert selection.selected == tuple(results)


def test_confidence_scorer_penalizes_limitations_and_empty_text():
    scorer = ConfidenceRelevanceScorer(limitation_penalty=0.3)
    topic = FakeTopic("t1", "x", "sports", kind=TopicKind.GENERAL_NEWS)

    limited = scorer.score(
        _result("t1", confidence=0.8, limitations=("타임라인 검증 불가",)), topic=topic
    )
    assert limited.score == pytest.approx(0.5)

    empty = scorer.score(_result("t1", text="   ", confidence=0.9), topic=topic)
    assert empty.score == 0.0
    assert empty.should_study is False


# --------------------------------------------------------------------------- #
# source policy / prompt 로더
# --------------------------------------------------------------------------- #


def test_load_source_policy_parses_categories_and_fallback():
    policy = load_source_policy(
        {
            "default_sources": {
                "sports": {
                    "collectors": ["realtime-lookup-skill", "web_search"],
                    "require_timeline_validation": True,
                },
            },
            "fallback": {"collectors": ["web_search"]},
        }
    )

    sports = policy.for_category("sports")
    assert sports.collectors == ("realtime-lookup-skill", "web_search")
    assert sports.require_timeline_validation is True
    assert policy.for_category("nope").collectors == ("web_search",)


def test_load_source_policy_rejects_category_without_collectors():
    with pytest.raises(ValueError):
        load_source_policy({"default_sources": {"bad": {"collectors": []}}})


def test_default_policy_marks_sports_for_timeline_validation():
    assert DEFAULT_SOURCE_POLICY.for_category("sports").require_timeline_validation


def test_news_relevance_prompt_loads_and_formats():
    spec = load_news_relevance_prompt()

    assert spec.name == "news_relevance"
    rendered = spec.format(
        user_interests="AI",
        topic_label="AI 반도체",
        topic_category="ai-industry",
        news_title="새 칩 발표",
        published_at="2026-06-29",
        news_summary="요약",
    )
    assert "AI 반도체" in rendered
    assert "새 칩 발표" in rendered
