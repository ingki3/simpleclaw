"""BIZ-480 realtime lookup 도메인 source 수집·파싱 회귀 테스트."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote_plus

import pytest

from simpleclaw.skills.realtime_sources import (
    CollectionOutcome,
    NewsCandidate,
    SportsGameFact,
    build_google_news_rss_url,
    build_naver_sports_schedule_url,
    build_sports_page_url,
    collect_sources,
    filter_recent_candidates,
    html_to_visible_text,
    parse_naver_kbo_schedule,
    parse_google_news_rss,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "realtime_lookup"
_RECENT_RSS = (_FIXTURES / "google_news_recent.xml").read_text(encoding="utf-8")
_STALE_RSS = (_FIXTURES / "google_news_stale.xml").read_text(encoding="utf-8")
_STARTED_JSON = (_FIXTURES / "naver_kbo_started.json").read_text(encoding="utf-8")
_ENDED_JSON = (_FIXTURES / "naver_kbo_ended.json").read_text(encoding="utf-8")
_RESULT_JSON = (_FIXTURES / "naver_kbo_result.json").read_text(encoding="utf-8")
_DOUBLEHEADER_JSON = (_FIXTURES / "naver_kbo_doubleheader.json").read_text(
    encoding="utf-8"
)
_NO_MATCH_JSON = (_FIXTURES / "naver_kbo_no_match.json").read_text(encoding="utf-8")
_MALFORMED_JSON = (_FIXTURES / "naver_kbo_malformed.json").read_text(encoding="utf-8")
_CANCELLED_JSON = (_FIXTURES / "naver_kbo_cancelled_suspended.json").read_text(
    encoding="utf-8"
)
_UNKNOWN_JSON = (_FIXTURES / "naver_kbo_unknown_status.json").read_text(
    encoding="utf-8"
)
_AS_OF = "2026-07-24T22:18:43+09:00"
_SPORTS_AS_OF = "2026-07-28T20:18:43+09:00"


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


def test_sports_schedule_url_is_date_bounded_without_status_filter():
    url = build_naver_sports_schedule_url(as_of_kst=_SPORTS_AS_OF)

    decoded = unquote_plus(url)
    assert url.startswith("https://api-gw.sports.naver.com/schedule/games?")
    assert "fromDate=2026-07-28" in decoded
    assert "toDate=2026-07-28" in decoded
    assert "size=20" in decoded
    assert "page=1" in decoded
    assert "fields=basic,schedule,baseball,manualRelayUrl" in decoded
    assert "upperCategoryId=kbaseball" in decoded
    assert "categoryIds=kbo" in decoded
    assert "statusCode" not in decoded
    # 기존 public helper는 호출자를 깨지 않고 동일 structured endpoint를 가리킨다.
    assert build_sports_page_url("ignored", as_of_kst=_SPORTS_AS_OF) == url


def test_html_to_visible_text_preserves_image_alt_markers():
    text = html_to_visible_text('<div><img alt="경기종료"><img alt="LIVE"></div>')

    assert "경기종료" in text
    assert "LIVE" in text


@pytest.mark.parametrize(
    ("body", "expected_status", "expected_winner", "expected_scores"),
    [
        (_STARTED_JSON, "live", None, (8, 3)),
        (_ENDED_JSON, "final", "롯데 자이언츠", (8, 3)),
        (_RESULT_JSON, "final", "한화 이글스", (2, 4)),
    ],
)
def test_parse_naver_kbo_schedule_uses_enums_not_status_info(
    body,
    expected_status,
    expected_winner,
    expected_scores,
):
    source_url = build_naver_sports_schedule_url(as_of_kst=_SPORTS_AS_OF)

    outcome = parse_naver_kbo_schedule(
        body,
        source_url=source_url,
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    assert outcome.lookup_status == "found"
    assert outcome.limitations == []
    fact = outcome.sources[0].sports_fact
    assert fact == SportsGameFact(
        league="KBO",
        event_date="2026-07-28",
        status=expected_status,
        away_team="롯데 자이언츠",
        away_score=expected_scores[0],
        home_team="한화 이글스",
        home_score=expected_scores[1],
        winner=expected_winner,
        source="Naver Sports Schedule API",
        source_url=source_url,
    )


@pytest.mark.parametrize("status_info", ["경기중", "임의 변경 문구", None])
def test_started_state_is_unchanged_when_status_info_changes(status_info):
    payload = json.loads(_STARTED_JSON)
    game = payload["result"]["games"][0]
    if status_info is None:
        game.pop("statusInfo", None)
    else:
        game["statusInfo"] = status_info

    outcome = parse_naver_kbo_schedule(
        json.dumps(payload, ensure_ascii=False),
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    fact = outcome.sources[0].sports_fact
    assert outcome.lookup_status == "found"
    assert fact is not None
    assert fact.status == "live"
    assert fact.winner is None


def test_doubleheader_prefers_later_started_game_over_earlier_result():
    outcome = parse_naver_kbo_schedule(
        _DOUBLEHEADER_JSON,
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    fact = outcome.sources[0].sports_fact
    assert outcome.lookup_status == "found"
    assert fact is not None
    assert fact.status == "live"
    assert (fact.away_score, fact.home_score) == (3, 1)
    assert fact.winner is None


def test_doubleheader_with_two_finals_selects_latest_game_datetime():
    payload = json.loads(_DOUBLEHEADER_JSON)
    later_game = payload["result"]["games"][1]
    later_game["statusCode"] = "RESULT"
    later_game["winner"] = "AWAY"

    outcome = parse_naver_kbo_schedule(
        json.dumps(payload, ensure_ascii=False),
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    fact = outcome.sources[0].sports_fact
    assert outcome.lookup_status == "found"
    assert fact is not None
    assert fact.status == "final"
    assert (fact.away_score, fact.home_score) == (3, 1)
    assert fact.winner == "롯데 자이언츠"


def test_multiple_started_games_use_game_id_as_deterministic_datetime_tie_breaker():
    payload = json.loads(_DOUBLEHEADER_JSON)
    earlier_game, later_game = payload["result"]["games"]
    earlier_game["statusCode"] = "STARTED"
    earlier_game["gameDateTime"] = later_game["gameDateTime"]

    outcome = parse_naver_kbo_schedule(
        json.dumps(payload, ensure_ascii=False),
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    fact = outcome.sources[0].sports_fact
    assert outcome.lookup_status == "found"
    assert fact is not None
    assert fact.status == "live"
    assert (fact.away_score, fact.home_score) == (3, 1)
    assert fact.winner is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("code", 500), ("success", False), ("success", 1)],
)
def test_parse_naver_kbo_schedule_rejects_invalid_live_envelope(field, value):
    payload = json.loads(_STARTED_JSON)
    payload[field] = value

    outcome = parse_naver_kbo_schedule(
        json.dumps(payload),
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    assert outcome.lookup_status == "failed"
    assert outcome.sources == []


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (_NO_MATCH_JSON, "not_found"),
        (_MALFORMED_JSON, "failed"),
        (_CANCELLED_JSON, "failed"),
        (_UNKNOWN_JSON, "failed"),
        ("not-json", "failed"),
    ],
)
def test_parse_naver_kbo_schedule_distinguishes_no_match_from_failure(
    body, expected_status
):
    outcome = parse_naver_kbo_schedule(
        body,
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    assert isinstance(outcome, CollectionOutcome)
    assert outcome.lookup_status == expected_status
    assert outcome.sources == []
    assert outcome.limitations


@pytest.mark.parametrize(("cancelled", "suspended"), [(True, False), (False, True)])
def test_parse_naver_kbo_schedule_rejects_cancelled_or_suspended(
    cancelled, suspended
):
    payload = json.loads(_CANCELLED_JSON)
    game = payload["result"]["games"][0]
    game["cancel"] = cancelled
    game["suspended"] = suspended

    outcome = parse_naver_kbo_schedule(
        json.dumps(payload),
        source_url="https://api-gw.sports.naver.com/schedule/games",
        expected_date="2026-07-28",
        expected_team="롯데",
    )

    assert outcome.lookup_status == "failed"
    assert outcome.sources == []


@pytest.mark.asyncio
async def test_sports_collection_fetches_only_dated_naver_schedule_api():
    url = build_naver_sports_schedule_url(as_of_kst=_SPORTS_AS_OF)
    fetch = FetchRecorder({url: _STARTED_JSON})

    outcome = await collect_sources(
        query="오늘 롯데 자이언츠 경기 결과",
        kind="sports",
        as_of_kst=_SPORTS_AS_OF,
        fetch_page=fetch,
    )

    assert fetch.urls == [url]
    assert outcome.lookup_status == "found"
    assert outcome.sources[0].source_kind == "sports_api"
    assert outcome.sources[0].sports_fact is not None
    assert outcome.sources[0].sports_fact.status == "live"
    assert outcome.sources[0].sports_fact.winner is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        (_NO_MATCH_JSON, "not_found"),
        (_MALFORMED_JSON, "failed"),
        (_UNKNOWN_JSON, "failed"),
        ("Error: upstream unavailable", "failed"),
    ],
)
async def test_sports_collection_exposes_typed_lookup_status(body, expected_status):
    url = build_naver_sports_schedule_url(as_of_kst=_SPORTS_AS_OF)
    outcome = await collect_sources(
        query="롯데 야구 결과",
        kind="sports",
        as_of_kst=_SPORTS_AS_OF,
        fetch_page=FetchRecorder({url: body}),
    )

    assert outcome.lookup_status == expected_status


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
