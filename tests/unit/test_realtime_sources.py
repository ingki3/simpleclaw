"""BIZ-480 realtime lookup 도메인 source 수집·파싱 회귀 테스트."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote_plus

import pytest

from simpleclaw.skills.realtime_sources import (
    NewsCandidate,
    SportsGameFact,
    build_google_news_rss_url,
    build_sports_page_url,
    collect_sources,
    filter_recent_candidates,
    html_to_visible_text,
    parse_google_news_rss,
    parse_naver_kbo_game_card,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "realtime_lookup"
_RECENT_RSS = (_FIXTURES / "google_news_recent.xml").read_text(encoding="utf-8")
_STALE_RSS = (_FIXTURES / "google_news_stale.xml").read_text(encoding="utf-8")
_FINAL_HTML = (_FIXTURES / "naver_kbo_final.html").read_text(encoding="utf-8")
_LIVE_HTML = (_FIXTURES / "naver_kbo_live.html").read_text(encoding="utf-8")
_AS_OF = "2026-07-24T22:18:43+09:00"


class FetchRecorder:
    """등록된 fixture 응답을 돌려주고 호출 URL 순서를 기록한다."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        return self.responses.get(url, "Error: fixture URL not registered")


def test_google_news_rss_url_is_freshness_bounded():
    url = build_google_news_rss_url("AI news", lookback_days=1)

    decoded = unquote_plus(url)
    assert url.startswith("https://news.google.com/rss/search?")
    assert "AI news when:1d" in decoded
    assert "hl=ko" in decoded
    assert "ceid=KR:ko" in decoded


def test_google_news_rss_parser_and_stale_filter_use_as_of_time():
    recent = parse_google_news_rss(_RECENT_RSS)
    stale = parse_google_news_rss(_STALE_RSS)

    assert recent[0] == NewsCandidate(
        title="검증 가능한 최신 AI 기사 - Example News",
        url="https://publisher.example/article-recent",
        source="Example News",
        published_at="2026-07-24T11:30:00+00:00",
        source_url="https://publisher.example/",
    )
    assert filter_recent_candidates(recent, as_of_kst=_AS_OF, max_age_hours=48)
    assert filter_recent_candidates(stale, as_of_kst=_AS_OF, max_age_hours=48) == []


@pytest.mark.asyncio
async def test_news_collection_uses_google_rss_then_fetches_original_body():
    rss_url = build_google_news_rss_url("AI news")
    article = "검증 가능한 원문 기사 본문입니다. " * 30
    fetch = FetchRecorder(
        {
            rss_url: _RECENT_RSS,
            "https://publisher.example/article-recent": article,
            "https://other.example/article-recent": "다른 원문 본문입니다. " * 50,
        }
    )

    sources, limitations = await collect_sources(
        query="AI news",
        kind="news",
        as_of_kst=_AS_OF,
        fetch_page=fetch,
    )

    assert [source.url for source in sources] == [
        "https://publisher.example/article-recent",
        "https://other.example/article-recent",
    ]
    assert all(source.source_kind == "news_article" for source in sources)
    assert fetch.urls[0] == rss_url
    assert all("duckduckgo.com" not in url for url in fetch.urls)
    assert limitations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "article_body",
    [
        "FETCH_BLOCKED: automated fetching is blocked",
        "Error: HTTP 403 — Forbidden",
        "짧은 본문",
    ],
)
async def test_news_rss_title_without_usable_article_body_produces_no_source(article_body):
    rss_url = build_google_news_rss_url("AI news")
    fetch = FetchRecorder(
        {
            rss_url: _RECENT_RSS,
            "https://publisher.example/article-recent": article_body,
            "https://other.example/article-recent": article_body,
        }
    )

    sources, limitations = await collect_sources(
        query="AI news",
        kind="news",
        as_of_kst=_AS_OF,
        fetch_page=fetch,
    )

    assert sources == []
    assert limitations


def test_sports_page_url_contains_as_of_date_and_team_query():
    url = build_sports_page_url("롯데 야구 어케 되었나?", as_of_kst=_AS_OF)

    decoded = unquote_plus(url)
    assert "search.naver.com/search.naver" in url
    assert "2026년 7월 24일" in decoded
    assert "롯데 자이언츠 경기 결과" in decoded
    assert "어케 되었나" not in decoded
    assert "경기 결과" in decoded


def test_html_to_visible_text_preserves_image_alt_markers():
    text = html_to_visible_text('<div><img alt="경기종료"><img alt="LIVE"></div>')

    assert "경기종료" in text
    assert "LIVE" in text


def test_parse_naver_kbo_final_card_extracts_one_exact_score_fact():
    source_url = build_sports_page_url("오늘 롯데 경기 결과", as_of_kst=_AS_OF)

    fact = parse_naver_kbo_game_card(
        _FINAL_HTML,
        source_url=source_url,
        expected_date="2026-07-24",
        expected_team="롯데 자이언츠",
    )

    assert fact == SportsGameFact(
        league="KBO",
        event_date="2026-07-24",
        status="final",
        away_team="kt wiz",
        away_score=5,
        home_team="롯데 자이언츠",
        home_score=4,
        winner="kt wiz",
        source="Naver Sports Game Card",
        source_url=source_url,
    )


def test_parse_naver_kbo_final_visible_text_from_builtin_fetch():
    """태그가 제거된 built-in web-fetch 본문에서도 날짜-bound 카드만 파싱한다."""
    visible_text = html_to_visible_text(_FINAL_HTML, limit=30_000)

    fact = parse_naver_kbo_game_card(
        visible_text,
        source_url="https://search.naver.com/static-text",
        expected_date="2026-07-24",
        expected_team="롯데",
    )

    assert fact is not None
    assert fact.status == "final"
    assert (fact.away_team, fact.away_score) == ("kt wiz", 5)
    assert (fact.home_team, fact.home_score) == ("롯데 자이언츠", 4)
    assert fact.winner == "kt wiz"


def test_parse_naver_kbo_live_card_never_marks_winner():
    fact = parse_naver_kbo_game_card(
        _LIVE_HTML,
        source_url="https://search.naver.com/live",
        expected_date="2026-07-24",
        expected_team="롯데",
    )

    assert fact is not None
    assert fact.status == "live"
    assert (fact.away_score, fact.home_score) == (2, 4)
    assert fact.winner is None


@pytest.mark.parametrize(
    ("expected_date", "expected_team"),
    [("2026-07-25", "롯데"), ("2026-07-24", "한화 이글스")],
)
def test_parse_naver_card_rejects_date_or_team_mismatch(expected_date, expected_team):
    assert (
        parse_naver_kbo_game_card(
            _FINAL_HTML,
            source_url="https://search.naver.com/final",
            expected_date=expected_date,
            expected_team=expected_team,
        )
        is None
    )


def test_parse_naver_card_never_scans_trailing_blog_without_card_boundary():
    page_without_card = (
        '<meta property="og:title" content="2026년 7월 24일 롯데 야구">'
        "<article>롯데 자이언츠가 2:1로 이겼다. 경기종료.</article>"
    )

    assert (
        parse_naver_kbo_game_card(
            page_without_card,
            source_url="https://search.naver.com/no-card",
            expected_date="2026-07-24",
            expected_team="롯데",
        )
        is None
    )


@pytest.mark.asyncio
async def test_sports_collection_fetches_only_dated_naver_game_page():
    url = build_sports_page_url("오늘 롯데 자이언츠 경기 결과", as_of_kst=_AS_OF)
    fetch = FetchRecorder({url: _FINAL_HTML})

    sources, limitations = await collect_sources(
        query="오늘 롯데 자이언츠 경기 결과",
        kind="sports",
        as_of_kst=_AS_OF,
        fetch_page=fetch,
    )

    assert fetch.urls == [url]
    assert sources[0].source_kind == "sports_page"
    assert sources[0].sports_fact is not None
    assert sources[0].sports_fact.away_score == 5
    assert sources[0].sports_fact.home_score == 4
    assert limitations == []


@pytest.mark.asyncio
async def test_google_news_standard_article_link_resolves_then_fetches_publisher_body():
    """실제 RSS의 Google article URL은 publisher URL 해석 후 원문을 읽는다."""
    rss_url = build_google_news_rss_url("AI news")
    google_url = "https://news.google.com/rss/articles/CBMi-real-token?oc=5"
    publisher_url = "https://publisher.example/original-article"
    xml = f"""<?xml version="1.0"?><rss><channel><item>
      <title>검증 가능한 최신 AI 기사 - Example News</title>
      <link>{google_url}</link>
      <pubDate>Fri, 24 Jul 2026 11:30:00 GMT</pubDate>
      <source url="https://publisher.example">Example News</source>
    </item></channel></rss>"""
    fetch = FetchRecorder({rss_url: xml, publisher_url: "검증된 publisher 원문입니다. " * 40})
    resolved = []

    async def resolve(candidate):
        resolved.append(candidate)
        return publisher_url

    sources, limitations = await collect_sources(
        query="AI news",
        kind="news",
        as_of_kst=_AS_OF,
        fetch_page=fetch,
        resolve_news_url=resolve,
    )

    assert resolved[0].url == google_url
    assert resolved[0].source_url == "https://publisher.example"
    assert fetch.urls == [rss_url, publisher_url]
    assert [source.url for source in sources] == [publisher_url]
    assert limitations == []


@pytest.mark.asyncio
async def test_google_news_standard_link_without_safe_resolution_fails_closed():
    rss_url = build_google_news_rss_url("AI news")
    google_url = "https://news.google.com/rss/articles/CBMi-real-token?oc=5"
    xml = f"""<?xml version="1.0"?><rss><channel><item>
      <title>검증 가능한 최신 AI 기사 - Example News</title>
      <link>{google_url}</link>
      <pubDate>Fri, 24 Jul 2026 11:30:00 GMT</pubDate>
      <source url="https://publisher.example">Example News</source>
    </item></channel></rss>"""
    fetch = FetchRecorder({rss_url: xml})

    async def reject(_candidate):
        return None

    sources, limitations = await collect_sources(
        query="AI news",
        kind="news",
        as_of_kst=_AS_OF,
        fetch_page=fetch,
        resolve_news_url=reject,
    )

    assert sources == []
    assert fetch.urls == [rss_url]
    assert any("publisher URL" in item for item in limitations)
