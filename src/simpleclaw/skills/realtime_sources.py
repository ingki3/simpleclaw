"""BIZ-480 realtime lookup의 도메인별 source collector.

뉴스/일반 최신 이슈는 Google News RSS를 후보 발견에만 사용하고, 후보 원문을
실제로 읽은 경우에만 source로 채택한다. KBO 스포츠는 요청 기준일로 제한한
네이버 스포츠 schedule JSON의 schema와 enum만 사용해 점수 fact를 구조화한다.
외부 parser 의존성 없이 stdlib만 사용한다.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from simpleclaw.skills.realtime_contracts import RealtimeLookupRequest

GOOGLE_NEWS_RSS_ENDPOINT = "https://news.google.com/rss/search"
_NAVER_SEARCH_ENDPOINT = "https://search.naver.com/search.naver"
NAVER_SPORTS_SCHEDULE_ENDPOINT = "https://api-gw.sports.naver.com/schedule/games"
_KST = ZoneInfo("Asia/Seoul")
_MIN_ARTICLE_CHARS = 400
_MIN_PAGE_CHARS = 120
_MAX_NEWS_SOURCES = 2
_MAX_SOURCE_CHARS = 8000

FetchPage = Callable[[str], Awaitable[str]]
ResolveNewsUrl = Callable[["NewsCandidate"], Awaitable[str | None]]


@dataclass(frozen=True)
class NewsCandidate:
    """Google News RSS가 발견한 원문 fetch 후보."""

    title: str
    url: str
    source: str
    published_at: str | None
    source_url: str = ""


@dataclass(frozen=True)
class SportsGameFact:
    """한 경기 카드에서 함께 추출한 KBO 점수·상태 fact."""

    league: str
    event_date: str
    status: str
    away_team: str
    away_score: int
    home_team: str
    home_score: int
    winner: str | None
    source: str
    source_url: str


@dataclass(frozen=True)
class SourceDocument:
    """검증을 통과해 realtime evidence로 사용할 수 있는 source body."""

    source: str
    url: str
    text: str
    source_kind: str
    title: str = ""
    published_at: str | None = None
    event_date: str | None = None
    sports_fact: SportsGameFact | None = None


@dataclass(frozen=True)
class CollectionOutcome:
    """명시적 no-match를 fetch/schema/state 실패와 분리한 수집 결과."""

    lookup_status: Literal[
        "found", "not_found", "failed", "unsupported", "unusable"
    ]
    sources: list[SourceDocument]
    limitations: list[str]

    def __iter__(self):
        """기존 ``sources, limitations`` 언패킹 호출자를 보존한다."""
        yield self.sources
        yield self.limitations


def build_google_news_rss_url(query: str, *, lookback_days: int = 1) -> str:
    """한국 locale의 freshness-bounded Google News RSS 검색 URL을 만든다."""
    bounded = f"{query.strip()} when:{max(1, lookback_days)}d".strip()
    return GOOGLE_NEWS_RSS_ENDPOINT + "?" + urlencode(
        {"q": bounded, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )


def _parse_published_at(raw: str) -> str | None:
    if not raw.strip():
        return None
    try:
        parsed = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _candidate_from_item(node: ET.Element) -> NewsCandidate | None:
    title = (node.findtext("title") or "").strip()
    url = (node.findtext("link") or "").strip()
    if not title or not url:
        return None
    source_node = node.find("source")
    source = (
        (source_node.text or "").strip() if source_node is not None else ""
    ) or "Google News"
    return NewsCandidate(
        title=title,
        url=url,
        source=source,
        published_at=_parse_published_at(node.findtext("pubDate") or ""),
        source_url=(source_node.get("url") or "").strip() if source_node is not None else "",
    )


def parse_google_news_rss(xml_text: str) -> list[NewsCandidate]:
    """Google News RSS item을 파싱한다.

    내장 ``web_fetch``는 긴 응답을 잘라 반환할 수 있으므로, 전체 XML이 중간에서
    잘렸을 때도 완결된 ``<item>...</item>`` 블록까지만 보수적으로 복구한다.
    """
    nodes: Iterable[ET.Element]
    try:
        root = ET.fromstring(xml_text)
        nodes = root.findall("./channel/item")
    except ET.ParseError:
        recovered: list[ET.Element] = []
        for block in re.findall(r"(?is)<item\b[^>]*>.*?</item>", xml_text):
            try:
                recovered.append(ET.fromstring(block))
            except ET.ParseError:
                continue
        nodes = recovered

    candidates: list[NewsCandidate] = []
    for node in nodes:
        candidate = _candidate_from_item(node)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _as_datetime(value: object) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=_KST)
            return parsed
        except ValueError:
            pass
    return datetime.now(_KST)


def filter_recent_candidates(
    candidates: Iterable[NewsCandidate],
    *,
    as_of_kst: object,
    max_age_hours: int = 48,
) -> list[NewsCandidate]:
    """발행시각이 as-of freshness window 안인 RSS 후보만 남긴다."""
    as_of = _as_datetime(as_of_kst).astimezone(UTC)
    oldest = as_of - timedelta(hours=max(1, max_age_hours))
    accepted: list[NewsCandidate] = []
    for candidate in candidates:
        if candidate.published_at is None:
            continue
        try:
            published = datetime.fromisoformat(candidate.published_at).astimezone(
                UTC
            )
        except ValueError:
            continue
        # 미래로 크게 튄 feed clock도 최신 기사로 인정하지 않는다.
        if oldest <= published <= as_of + timedelta(hours=1):
            accepted.append(candidate)
    return accepted


def _as_of_date(as_of_kst: object) -> datetime:
    return _as_datetime(as_of_kst).astimezone(_KST)


def build_naver_sports_schedule_url(*, as_of_kst: object) -> str:
    """상태 필터 없이 기준일 하루의 KBO schedule API URL을 만든다."""
    selected_date = _as_of_date(as_of_kst).date().isoformat()
    return NAVER_SPORTS_SCHEDULE_ENDPOINT + "?" + urlencode(
        {
            "fromDate": selected_date,
            "toDate": selected_date,
            "size": "20",
            "page": "1",
            "fields": "basic,schedule,baseball,manualRelayUrl",
            "upperCategoryId": "kbaseball",
            "categoryIds": "kbo",
        }
    )


def build_sports_page_url(query: str, *, as_of_kst: object) -> str:
    """기존 호출자를 위해 structured schedule URL helper를 유지한다."""
    del query
    return build_naver_sports_schedule_url(as_of_kst=as_of_kst)


def build_naver_search_url(query: str) -> str:
    """날씨 등 비뉴스 source의 직접 네이버 검색 URL을 만든다."""
    return _NAVER_SEARCH_ENDPOINT + "?" + urlencode(
        {"where": "nexearch", "query": query.strip()}
    )


def html_to_visible_text(body: str, *, limit: int = _MAX_SOURCE_CHARS) -> str:
    """HTML의 이미지 alt를 보존하고 script/chrome/tag를 제거한다."""
    body = re.sub(
        r"(?is)<img\b[^>]*\balt=[\"']([^\"']+)[\"'][^>]*>",
        r" \1 ",
        body,
    )
    body = re.sub(r"(?is)<script\b.*?</script>", " ", body)
    body = re.sub(r"(?is)<style\b.*?</style>", " ", body)
    body = re.sub(r"(?is)<(nav|header|footer|aside|form)\b.*?</\1>", " ", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html.unescape(body)).strip()[:limit]


def _looks_like_fetch_failure(body: str) -> bool:
    stripped = body.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return stripped.startswith("Error:") or any(
        marker in lowered
        for marker in (
            "fetch_blocked:",
            "automated fetching",
            "automated traffic",
            "access denied",
            "verify you are human",
            "checking your browser",
        )
    )


def _is_google_news_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "news.google.com" or host.endswith(".news.google.com")


def _looks_like_google_news_shell(body: str) -> bool:
    """Google News chrome/redirect shell을 publisher 원문으로 오인하지 않는다."""
    lowered = body.lower()
    strong_markers = (
        "<title>google news</title>",
        "<base href=\"https://news.google.com",
        "<base href='https://news.google.com",
        "news.google.com/articles/",
        "google 뉴스에서 전체 기사 보기",
    )
    return any(marker in lowered for marker in strong_markers)


_TEAM_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kt wiz", ("kt wiz", "kt 위즈", "케이티 위즈", "kt")),
    ("롯데 자이언츠", ("롯데 자이언츠", "롯데")),
    ("KIA 타이거즈", ("kia 타이거즈", "기아 타이거즈", "kia")),
    ("LG 트윈스", ("lg 트윈스", "lg")),
    ("NC 다이노스", ("nc 다이노스", "nc")),
    ("SSG 랜더스", ("ssg 랜더스", "ssg")),
    ("두산 베어스", ("두산 베어스", "두산")),
    ("삼성 라이온즈", ("삼성 라이온즈", "삼성")),
    ("키움 히어로즈", ("키움 히어로즈", "키움")),
    ("한화 이글스", ("한화 이글스", "한화")),
)


def canonical_kbo_team(text: str) -> str | None:
    """질문/카드 표기의 KBO 팀 alias를 canonical 이름으로 바꾼다."""
    lowered = text.lower()
    matches: list[tuple[int, int, str]] = []
    for canonical, aliases in _TEAM_ALIASES:
        for alias in aliases:
            index = lowered.find(alias.lower())
            if index >= 0:
                matches.append((index, -len(alias), canonical))
    if not matches:
        return None
    return min(matches)[2]


def _failed_sports_outcome(message: str) -> CollectionOutcome:
    """스포츠 schema/state 오류를 일관된 fail-closed 결과로 만든다."""
    return CollectionOutcome("failed", [], [message])


def parse_naver_kbo_schedule(
    body: str,
    *,
    source_url: str,
    expected_date: str,
    expected_team: str,
) -> CollectionOutcome:
    """네이버 KBO schedule JSON에서 구조화 필드로 대상 경기를 결정한다."""
    expected_canonical = canonical_kbo_team(expected_team)
    if expected_canonical is None:
        return _failed_sports_outcome("질문에서 확인할 KBO 팀을 특정하지 못했습니다.")
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return _failed_sports_outcome("네이버 스포츠 응답이 유효한 JSON이 아닙니다.")
    if (
        not isinstance(payload, dict)
        or payload.get("code") != 200
        or payload.get("success") is not True
        or not isinstance(payload.get("result"), dict)
    ):
        return _failed_sports_outcome("네이버 스포츠 응답 envelope가 올바르지 않습니다.")
    games = payload["result"].get("games")
    if not isinstance(games, list) or not all(isinstance(game, dict) for game in games):
        return _failed_sports_outcome("네이버 스포츠 result.games 배열이 올바르지 않습니다.")
    if any(game.get("gameDate") != expected_date for game in games):
        return _failed_sports_outcome(
            "날짜 제한 요청과 다른 gameDate가 네이버 스포츠 응답에 포함됐습니다."
        )
    if any(game.get("categoryId") != "kbo" for game in games):
        return _failed_sports_outcome(
            "KBO 제한 요청과 다른 categoryId가 네이버 스포츠 응답에 포함됐습니다."
        )

    candidates: list[tuple[bool, datetime, str, SourceDocument]] = []
    for game in games:
        away_team = canonical_kbo_team(str(game.get("awayTeamName") or ""))
        home_team = canonical_kbo_team(str(game.get("homeTeamName") or ""))
        if away_team is None or home_team is None or away_team == home_team:
            return _failed_sports_outcome("KBO 경기의 팀 schema가 올바르지 않습니다.")
        if expected_canonical not in {away_team, home_team}:
            continue
        if game.get("cancel") is not False or game.get("suspended") is not False:
            return _failed_sports_outcome("대상 경기가 취소 또는 중단 상태입니다.")

        game_id = game.get("gameId")
        game_datetime_raw = game.get("gameDateTime")
        if (
            not isinstance(game_id, str)
            or not game_id
            or game_id != game_id.strip()
        ):
            return _failed_sports_outcome("대상 경기의 gameId schema가 올바르지 않습니다.")
        if not isinstance(game_datetime_raw, str) or "T" not in game_datetime_raw:
            return _failed_sports_outcome(
                "대상 경기의 gameDateTime schema가 올바르지 않습니다."
            )
        try:
            game_datetime = datetime.fromisoformat(game_datetime_raw)
        except ValueError:
            return _failed_sports_outcome(
                "대상 경기의 gameDateTime schema가 올바르지 않습니다."
            )
        if (
            game_datetime.tzinfo is not None
            or game_datetime.date().isoformat() != expected_date
        ):
            return _failed_sports_outcome(
                "대상 경기의 gameDateTime schema가 올바르지 않습니다."
            )

        away_score = game.get("awayTeamScore")
        home_score = game.get("homeTeamScore")
        if (
            not isinstance(away_score, int)
            or isinstance(away_score, bool)
            or not isinstance(home_score, int)
            or isinstance(home_score, bool)
            or away_score < 0
            or home_score < 0
        ):
            return _failed_sports_outcome("대상 경기의 점수 schema가 올바르지 않습니다.")

        status_code = game.get("statusCode")
        if status_code == "STARTED":
            status = "live"
            winner = None
        elif status_code in {"ENDED", "RESULT"}:
            status = "final"
            winner_code = game.get("winner")
            if winner_code == "AWAY":
                winner = away_team
            elif winner_code == "HOME":
                winner = home_team
            elif winner_code == "DRAW" and away_score == home_score:
                winner = None
            else:
                return _failed_sports_outcome(
                    "대상 경기의 final winner schema가 올바르지 않습니다."
                )
        else:
            return _failed_sports_outcome("대상 경기의 statusCode를 지원하지 않습니다.")

        fact = SportsGameFact(
            league="KBO",
            event_date=expected_date,
            status=status,
            away_team=away_team,
            away_score=away_score,
            home_team=home_team,
            home_score=home_score,
            winner=winner,
            source="Naver Sports Schedule API",
            source_url=source_url,
        )
        document = SourceDocument(
            source=fact.source,
            url=source_url,
            text=(
                f"{fact.event_date} {fact.away_team} {fact.away_score} : "
                f"{fact.home_score} {fact.home_team}"
            ),
            source_kind="sports_api",
            event_date=expected_date,
            sports_fact=fact,
        )
        candidates.append((status == "live", game_datetime, game_id, document))

    if candidates:
        started = [candidate for candidate in candidates if candidate[0]]
        selected = max(started or candidates, key=lambda candidate: candidate[1:3])
        return CollectionOutcome("found", [selected[3]], [])

    return CollectionOutcome(
        "not_found",
        [],
        ["정상 응답에 기준일·KBO·대상 팀이 모두 일치하는 경기가 없습니다."],
    )


async def _collect_news_sources(
    *,
    query: str,
    as_of_kst: object,
    fetch_page: FetchPage,
    resolve_news_url: ResolveNewsUrl | None,
) -> tuple[list[SourceDocument], list[str]]:
    limitations: list[str] = []
    rss_url = build_google_news_rss_url(query)
    rss_body = await fetch_page(rss_url)
    if _looks_like_fetch_failure(rss_body):
        return [], ["Google News RSS를 읽지 못했습니다."]
    candidates = parse_google_news_rss(rss_body)
    recent = filter_recent_candidates(
        candidates,
        as_of_kst=as_of_kst,
        max_age_hours=48,
    )
    if not recent:
        return [], ["기준시각 48시간 안에 발행된 Google News RSS 후보가 없습니다."]

    sources: list[SourceDocument] = []
    for candidate in recent[:_MAX_NEWS_SOURCES]:
        # Google News RSS article token은 publisher URL이 아니다. RSS source
        # 홈페이지에서 same-site 제목 anchor를 안전하게 확인한 뒤 원문만 fetch한다.
        target_url = candidate.url
        if _is_google_news_url(candidate.url):
            target_url = (
                await resolve_news_url(candidate) if resolve_news_url is not None else None
            )
            if not target_url:
                limitations.append(
                    "Google News 후보의 publisher URL을 안전하게 확인하지 못함: "
                    f"{candidate.source}"
                )
                continue
        body = await fetch_page(target_url)
        if _looks_like_fetch_failure(body):
            limitations.append(f"원문 fetch 실패: {candidate.source}")
            continue
        if _looks_like_google_news_shell(body):
            limitations.append(
                f"Google News shell은 원문으로 인정하지 않음: {candidate.source}"
            )
            continue
        text = html_to_visible_text(body) if "<" in body and ">" in body else body.strip()
        if len(text) < _MIN_ARTICLE_CHARS:
            limitations.append(f"원문 본문이 너무 짧음: {candidate.source}")
            continue
        sources.append(
            SourceDocument(
                source=candidate.source,
                url=target_url,
                text=text[:_MAX_SOURCE_CHARS],
                source_kind="news_article",
                title=candidate.title,
                published_at=candidate.published_at,
            )
        )
    if not sources and not limitations:
        limitations.append("RSS 후보 원문을 확보하지 못했습니다.")
    return sources, limitations


async def _collect_sports_source(
    *,
    expected_team: str,
    as_of_kst: object,
    fetch_page: FetchPage,
) -> CollectionOutcome:
    expected_team = canonical_kbo_team(expected_team)
    if expected_team is None:
        return _failed_sports_outcome("질문에서 확인할 KBO 팀을 특정하지 못했습니다.")
    date = _as_of_date(as_of_kst)
    expected_date = date.date().isoformat()
    url = build_naver_sports_schedule_url(as_of_kst=as_of_kst)
    body = await fetch_page(url)
    if _looks_like_fetch_failure(body):
        return _failed_sports_outcome("날짜가 지정된 네이버 스포츠 API를 읽지 못했습니다.")
    return parse_naver_kbo_schedule(
        body,
        source_url=url,
        expected_date=expected_date,
        expected_team=expected_team,
    )

async def _collect_direct_search_source(
    *,
    query: str,
    kind: str,
    fetch_page: FetchPage,
) -> tuple[list[SourceDocument], list[str]]:
    url = build_naver_search_url(query)
    body = await fetch_page(url)
    if _looks_like_fetch_failure(body):
        return [], ["직접 검색 페이지를 읽지 못했습니다."]
    text = html_to_visible_text(body) if "<" in body and ">" in body else body.strip()
    if len(text) < _MIN_PAGE_CHARS:
        return [], ["직접 검색 페이지의 읽을 수 있는 본문이 너무 짧습니다."]
    return [
        SourceDocument(
            source="Naver Search",
            url=url,
            text=text[:_MAX_SOURCE_CHARS],
            source_kind=f"{kind}_page",
        )
    ], []


async def collect_sources(
    *,
    query: str,
    kind: str,
    as_of_kst: object,
    fetch_page: FetchPage,
    resolve_news_url: ResolveNewsUrl | None = None,
) -> CollectionOutcome:
    """도메인별 source policy에 따라 검증된 원문 source만 반환한다."""
    if kind == "sports":
        return await _collect_sports_source(
            expected_team=query,
            as_of_kst=as_of_kst,
            fetch_page=fetch_page,
        )
    if kind in {"news", "general", "market"}:
        sources, limitations = await _collect_news_sources(
            query=query,
            as_of_kst=as_of_kst,
            fetch_page=fetch_page,
            resolve_news_url=resolve_news_url,
        )
    else:
        sources, limitations = await _collect_direct_search_source(
            query=query,
            kind=kind,
            fetch_page=fetch_page,
        )
    return CollectionOutcome(
        "found" if sources else "failed",
        sources,
        limitations,
    )


async def collect_sources_for_request(
    request: RealtimeLookupRequest,
    *,
    fetch_page: FetchPage,
    resolve_news_url: ResolveNewsUrl | None = None,
) -> CollectionOutcome:
    """Select a source adapter from typed plan metadata only."""
    domain = request.domain.casefold()
    as_of = request.reference_date or request.as_of_kst
    if domain == "sports":
        league = request.entity("league").casefold()
        if league != "kbo":
            return CollectionOutcome(
                "unsupported",
                [],
                [
                    (
                        "No registered structured realtime adapter for "
                        f"sports league {request.entity('league') or 'unknown'}."
                    )
                ],
            )
        team = request.entity("team")
        if not team:
            return CollectionOutcome(
                "failed",
                [],
                ["The KBO adapter requires a typed team entity."],
            )
        return await _collect_sports_source(
            expected_team=team,
            as_of_kst=as_of,
            fetch_page=fetch_page,
        )

    news_backed_domains = frozenset(
        {
            "news",
            "general",
            "market",
            "entertainment",
            "technology",
            "finance",
        }
    )
    if domain in news_backed_domains:
        sources, limitations = await _collect_news_sources(
            query=request.query,
            as_of_kst=as_of,
            fetch_page=fetch_page,
            resolve_news_url=resolve_news_url,
        )
        return CollectionOutcome(
            "found" if sources else "failed",
            sources,
            limitations,
        )
    if domain == "weather":
        sources, limitations = await _collect_direct_search_source(
            query=request.query,
            kind=domain,
            fetch_page=fetch_page,
        )
        return CollectionOutcome(
            "found" if sources else "failed",
            sources,
            limitations,
        )
    return CollectionOutcome(
        "unsupported",
        [],
        [f"No registered realtime adapter for domain {request.domain}."],
    )


__all__ = [
    "CollectionOutcome",
    "FetchPage",
    "NewsCandidate",
    "ResolveNewsUrl",
    "SourceDocument",
    "SportsGameFact",
    "build_google_news_rss_url",
    "build_naver_search_url",
    "build_naver_sports_schedule_url",
    "build_sports_page_url",
    "canonical_kbo_team",
    "collect_sources",
    "collect_sources_for_request",
    "filter_recent_candidates",
    "html_to_visible_text",
    "parse_google_news_rss",
    "parse_naver_kbo_schedule",
]
