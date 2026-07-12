"""topic 별 search_queries 가 label 대신 fetch 쿼리로 쓰이는지 검증 (BIZ-434).

배경: live daily run 에서 `market-reports-us-kr` 의 display label
("US/Korean market reports and watchpoints")이 그대로 Google News RSS 쿼리로
쓰여 zero-result 가 반복됐다. display label 과 검색 쿼리를 분리해 US/KR 쿼리를
따로 태울 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from simpleclaw.study.source_planner import TopicKind, plan_fetch_requests
from simpleclaw.study.types import StudyTopic


@dataclass
class TopicWithQueries:
    """StudyTopic Protocol + search_queries 를 만족하는 테스트용 topic."""

    topic_id: str = "market-reports-us-kr"
    label: str = "US/Korean market reports and watchpoints"
    category: str = "markets"
    kind: TopicKind = TopicKind.USER_INTEREST
    max_sources: int = 2
    freshness_hours: int = 24
    search_queries: list[str] = field(
        default_factory=lambda: [
            "US stock market latest news",
            "KOSPI Korean stock market latest news",
            "한국 증시 코스피 시장 뉴스",
        ]
    )


def test_plan_uses_search_queries_instead_of_internal_label():
    requests = plan_fetch_requests([TopicWithQueries()])

    queries = [request.query for request in requests]
    assert "US/Korean market reports and watchpoints" not in queries
    assert "US stock market latest news" in queries
    assert "KOSPI Korean stock market latest news" in queries
    assert "한국 증시 코스피 시장 뉴스" in queries


def test_plan_pairs_each_search_query_with_category_collectors():
    requests = plan_fetch_requests([TopicWithQueries()])

    # markets 기본 collector 는 us-stock-skill, kr-stock-skill, news-search-skill.
    assert len(requests) == 9
    assert requests[0].collector == "us-stock-skill"
    assert requests[0].query == "US stock market latest news"
    assert requests[-1].collector == "news-search-skill"
    assert requests[-1].query == "한국 증시 코스피 시장 뉴스"


def test_plan_falls_back_to_label_without_search_queries():
    @dataclass
    class PlainTopic:
        topic_id: str = "t"
        label: str = "AI 반도체"
        category: str = "ai-industry"
        kind: TopicKind = TopicKind.USER_INTEREST
        max_sources: int = 3
        freshness_hours: int = 24

    requests = plan_fetch_requests([PlainTopic()])

    assert all(r.query == "AI 반도체" for r in requests)


def test_plan_ignores_blank_or_non_string_search_queries():
    topic = TopicWithQueries(search_queries=["  ", ""])

    requests = plan_fetch_requests([topic])

    # 유효 쿼리가 없으면 label 폴백.
    assert all(r.query == topic.label for r in requests)


def test_study_topic_dataclass_with_queries_plans_by_query():
    """types.StudyTopic 기반 record 도 search_queries 를 그대로 쓸 수 있다."""

    topic = StudyTopic(
        id="market-reports-us-kr",
        label="US/Korean market reports and watchpoints",
        category="markets",
        search_queries=["US stock market latest news"],
    )

    @dataclass
    class Adapter:
        topic_id: str
        label: str
        category: str
        kind: TopicKind
        max_sources: int
        freshness_hours: int
        search_queries: list[str]

    adapter = Adapter(
        topic_id=topic.id,
        label=topic.label,
        category=topic.category,
        kind=TopicKind(topic.kind),
        max_sources=2,
        freshness_hours=24,
        search_queries=topic.search_queries,
    )

    requests = plan_fetch_requests([adapter])
    assert {r.query for r in requests} == {"US stock market latest news"}
