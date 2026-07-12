"""Study collector adapter 검증 — RSS/web_search 주입식 수집 (BIZ-434).

live bridge 의 Google News RSS 수집이 package collector 로 이동하면서, 네트워크
없이 fake 콜백으로 파싱/0건/실패 처리를 고정한다.
"""

from __future__ import annotations

from simpleclaw.study.collector_adapters import (
    CallbackWebSearchCollector,
    GoogleNewsRSSCollector,
)
from simpleclaw.study.collectors import StudyFetchRequest


def _request(query: str = "US stock market latest news") -> StudyFetchRequest:
    return StudyFetchRequest(
        topic_id="market-reports-us-kr",
        query=query,
        collector="google-news-rss",
        max_sources=3,
        freshness_hours=24,
    )


def test_google_news_rss_collector_parses_json_items():
    captured_argv: list[list[str]] = []

    def fake_run(argv):
        captured_argv.append(list(argv))
        return {
            "ok": True,
            "items": [
                {
                    "title": "Market news",
                    "url": "https://example.com/a",
                    "snippet": "summary",
                    "published_at": "2026-07-12T00:00:00+09:00",
                }
            ],
        }

    collector = GoogleNewsRSSCollector(run_json=fake_run)
    results = collector.fetch(_request())

    assert len(results) == 1
    assert results[0].title == "Market news"
    assert results[0].url == "https://example.com/a"
    assert results[0].source == "google-news-rss"
    assert results[0].confidence >= 0.7
    # 요청 쿼리가 skill 인자로 전달된다.
    assert "US stock market latest news" in captured_argv[0]


def test_google_news_rss_collector_zero_items_returns_empty():
    def fake_run(argv):
        return {"ok": True, "items": []}

    collector = GoogleNewsRSSCollector(run_json=fake_run)

    assert collector.fetch(_request("bad query")) == []


def test_google_news_rss_collector_failure_returns_empty():
    def fake_run(argv):
        return {"ok": False, "error": "network down"}

    collector = GoogleNewsRSSCollector(run_json=fake_run)

    assert collector.fetch(_request()) == []


def test_google_news_rss_collector_caps_results_at_max_sources():
    def fake_run(argv):
        return {
            "ok": True,
            "items": [
                {"title": f"item-{i}", "snippet": "s", "url": f"https://e.com/{i}"}
                for i in range(10)
            ],
        }

    collector = GoogleNewsRSSCollector(run_json=fake_run)
    results = collector.fetch(_request())

    assert len(results) == 3  # request.max_sources


def test_callback_web_search_collector_normalizes_rows():
    def fake_search(query: str, max_results: int):
        return [
            {"title": "hit", "snippet": "body", "url": "https://example.com/h"},
            {"no_title": True},  # title/text 없는 row 는 버린다
        ]

    collector = CallbackWebSearchCollector(search=fake_search)
    results = collector.fetch(_request())

    assert len(results) == 1
    assert results[0].title == "hit"
    assert results[0].source == "web_search"


def test_callback_web_search_collector_isolates_callback_failure():
    def boom(query: str, max_results: int):
        raise RuntimeError("tool unavailable")

    collector = CallbackWebSearchCollector(search=boom)

    assert collector.fetch(_request()) == []
