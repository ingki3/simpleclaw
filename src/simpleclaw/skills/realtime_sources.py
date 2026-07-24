"""BIZ-480 realtime lookup의 도메인별 source collector.

뉴스/일반 최신 이슈는 Google News RSS를 후보 발견에만 사용하고, 후보 원문을
실제로 읽은 경우에만 source로 채택한다. 스포츠는 요청 기준일이 들어간 네이버
경기정보 검색 페이지 한 장을 읽고, 경기 목록 카드 경계 안에서만 점수 fact를
구조화한다. 외부 HTML parser 의존성 없이 stdlib만 사용한다.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

GOOGLE_NEWS_RSS_ENDPOINT = "https://news.google.com/rss/search"
_NAVER_SEARCH_ENDPOINT = "https://search.naver.com/search.naver"
_KST = ZoneInfo("Asia/Seoul")
_MIN_ARTICLE_CHARS = 400
_MIN_PAGE_CHARS = 120
_MAX_NEWS_SOURCES = 2
_MAX_SOURCE_CHARS = 8000

FetchPage = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class NewsCandidate:
    """Google News RSS가 발견한 원문 fetch 후보."""

    title: str
    url: str
    source: str
    published_at: str | None


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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


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
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
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
    as_of = _as_datetime(as_of_kst).astimezone(timezone.utc)
    oldest = as_of - timedelta(hours=max(1, max_age_hours))
    accepted: list[NewsCandidate] = []
    for candidate in candidates:
        if candidate.published_at is None:
            continue
        try:
            published = datetime.fromisoformat(candidate.published_at).astimezone(
                timezone.utc
            )
        except ValueError:
            continue
        # 미래로 크게 튄 feed clock도 최신 기사로 인정하지 않는다.
        if oldest <= published <= as_of + timedelta(hours=1):
            accepted.append(candidate)
    return accepted


def _as_of_date(as_of_kst: object) -> datetime:
    return _as_datetime(as_of_kst).astimezone(_KST)


def build_sports_page_url(query: str, *, as_of_kst: object) -> str:
    """요청 기준일을 명시한 네이버 경기정보 검색 페이지 URL을 만든다."""
    date = _as_of_date(as_of_kst)
    # 구어체 질문 전체를 넣으면 네이버가 일반 web 결과만 반환하고 공식 경기 widget을
    # 생략할 수 있다. 질문에서 팀을 정규화해 날짜+팀+결과의 최소 query를 사용한다.
    target = canonical_kbo_team(query) or query.strip()
    dated_query = f"{date.year}년 {date.month}월 {date.day}일 {target} 경기 결과".strip()
    return _NAVER_SEARCH_ENDPOINT + "?" + urlencode(
        {"where": "nexearch", "query": dated_query}
    )


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


def _team_mentions(text: str) -> list[tuple[int, str]]:
    lowered = text.lower()
    mentions: list[tuple[int, int, str]] = []
    for canonical, aliases in _TEAM_ALIASES:
        best: tuple[int, int, str] | None = None
        for alias in aliases:
            for match in re.finditer(re.escape(alias.lower()), lowered):
                candidate = (match.start(), -len(alias), canonical)
                if best is None or candidate < best:
                    best = candidate
        if best is not None:
            mentions.append(best)
    mentions.sort()
    return [(index, canonical) for index, _neg_len, canonical in mentions]


def _extract_card_scope(body: str) -> str | None:
    """경기 목록 table/text 경계만 반환하고 페이지 뒤 콘텐츠는 버린다."""
    if re.search(r"(?is)<table\b", body):
        for table in re.findall(r"(?is)<table\b.*?</table>", body):
            if not re.search(r"(?is)<caption\b[^>]*>\s*경기 목록\s*</caption>", table):
                continue
            tbody = re.search(
                r"(?is)<tbody\b[^>]*class=[\"'][^\"']*_scroll_content[^\"']*[\"'][^>]*>"
                r"(.*?)</tbody>",
                table,
            )
            if tbody is not None:
                return html_to_visible_text(tbody.group(1), limit=30_000)
        return None

    start = body.find("경기 목록")
    if start < 0:
        return None
    end_markers = ("전체일정보기", "최종업데이트 날짜", "스포츠 정보")
    ends = [body.find(marker, start + len("경기 목록")) for marker in end_markers]
    valid_ends = [end for end in ends if end > start]
    if not valid_ends:
        return None
    return re.sub(r"\s+", " ", body[start : min(valid_ends)]).strip()


def _extract_target_date_section(card_text: str, expected_date: str) -> str | None:
    try:
        expected = datetime.strptime(expected_date, "%Y-%m-%d")
    except ValueError:
        return None
    target = f"{expected.month:02d}.{expected.day:02d}."
    matches = list(re.finditer(r"(?<!\d)(\d{2})\.(\d{2})\.", card_text))
    for index, match in enumerate(matches):
        if match.group(0) != target:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(card_text)
        return card_text[match.end() : end].strip()
    return None


def _full_date_is_present(body: str, expected_date: str) -> bool:
    try:
        date = datetime.strptime(expected_date, "%Y-%m-%d")
    except ValueError:
        return False
    patterns = (
        f"{date.year}.{date.month:02d}.{date.day:02d}",
        f"{date.year}년 {date.month}월 {date.day}일",
        f"{date.year}년{date.month}월{date.day}일",
    )
    normalized = re.sub(r"\s+", " ", html.unescape(body))
    return any(pattern in normalized for pattern in patterns)


def _score_card_parts(
    section: str,
    expected_team: str,
) -> tuple[str, int, str, int, str, str] | None:
    """한 팀 쌍 경계 안에서 팀·점수와 점수 앞뒤 상태 문구를 찾는다."""
    mentions = _team_mentions(section)
    if len(mentions) < 2:
        return None

    # ``5 : 4``처럼 구분자가 있는 표/텍스트를 먼저 사용한다.
    for score_match in re.finditer(
        r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)", section
    ):
        before = [mention for mention in mentions if mention[0] < score_match.start()]
        after = [mention for mention in mentions if mention[0] > score_match.end()]
        if not before or not after:
            continue
        away_start, away_team = before[-1]
        home_start, home_team = after[0]
        if away_team == home_team or expected_team not in {away_team, home_team}:
            continue
        next_starts = [start for start, _team in mentions if start > home_start]
        game_end = next_starts[0] if next_starts else len(section)
        status_start = max(0, away_start - 40)
        return (
            away_team,
            int(score_match.group(1)),
            home_team,
            int(score_match.group(2)),
            section[status_start : score_match.start()],
            section[score_match.end() : game_end],
        )

    # handle_web_fetch visible text는 score cell의 colon을 잃고
    # ``kt wiz 승 로건 5 4 롯데 자이언츠 패``처럼 반환할 수 있다. 인접한 두 팀
    # 사이의 마지막 두 정수만 점수로 사용하며 다음 카드나 페이지 본문은 보지 않는다.
    for index in range(len(mentions) - 1):
        away_start, away_team = mentions[index]
        home_start, home_team = mentions[index + 1]
        if away_team == home_team or expected_team not in {away_team, home_team}:
            continue
        between = section[away_start:home_start]
        number_matches = list(re.finditer(r"(?<!\d)(\d{1,2})(?!\d)", between))
        if len(number_matches) < 2:
            continue
        away_match, home_match = number_matches[-2:]
        next_start = mentions[index + 2][0] if index + 2 < len(mentions) else len(section)
        status_start = max(0, away_start - 40)
        return (
            away_team,
            int(away_match.group(1)),
            home_team,
            int(home_match.group(1)),
            section[status_start : away_start + away_match.start()],
            section[away_start + home_match.end() : next_start],
        )
    return None


def parse_naver_kbo_game_card(
    body: str,
    *,
    source_url: str,
    expected_date: str,
    expected_team: str,
) -> SportsGameFact | None:
    """날짜-bound 네이버 경기 목록의 한 카드에서만 점수 fact를 추출한다."""
    if not _full_date_is_present(body, expected_date):
        return None
    expected_canonical = canonical_kbo_team(expected_team)
    if expected_canonical is None:
        return None
    card_scope = _extract_card_scope(body)
    if card_scope is None:
        return None
    section = _extract_target_date_section(card_scope, expected_date)
    if not section:
        return None

    score_parts = _score_card_parts(section, expected_canonical)
    if score_parts is None:
        return None
    away_team, away_score, home_team, home_score, before_score, after_score = score_parts
    game_text = f"{before_score} {after_score}"
    lowered = game_text.lower()
    is_live = any(
        cue in lowered for cue in ("live", "진행 중", "진행중", "현재 스코어")
    ) or bool(re.search(r"\b\d{1,2}회\b", game_text))
    left_won = "승" in before_score and "패" in after_score
    right_won = "패" in before_score and "승" in after_score
    is_final = (
        not is_live
        and (
            (left_won or right_won)
            or "경기종료" in game_text
            or "final" in lowered
        )
    )
    if not is_live and not is_final:
        return None

    winner: str | None = None
    if is_final:
        if left_won:
            winner = away_team
        elif right_won:
            winner = home_team
        elif away_score != home_score:
            winner = away_team if away_score > home_score else home_team

    return SportsGameFact(
        league="KBO",
        event_date=expected_date,
        status="live" if is_live else "final",
        away_team=away_team,
        away_score=away_score,
        home_team=home_team,
        home_score=home_score,
        winner=winner,
        source="Naver Sports Game Card",
        source_url=source_url,
    )


async def _collect_news_sources(
    *,
    query: str,
    as_of_kst: object,
    fetch_page: FetchPage,
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
        # Google News RSS의 news.google.com 링크는 publisher URL이 아니다. FetchPage
        # 계약에는 최종 redirect URL이 없으므로 실제 원문 URL을 검증하지 못하면 닫힌다.
        if _is_google_news_url(candidate.url):
            limitations.append(
                "Google News redirect에서 실제 publisher URL을 확인하지 못함: "
                f"{candidate.source}"
            )
            continue
        body = await fetch_page(candidate.url)
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
                url=candidate.url,
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
    query: str,
    as_of_kst: object,
    fetch_page: FetchPage,
) -> tuple[list[SourceDocument], list[str]]:
    expected_team = canonical_kbo_team(query)
    if expected_team is None:
        return [], ["질문에서 확인할 KBO 팀을 특정하지 못했습니다."]
    date = _as_of_date(as_of_kst)
    expected_date = date.date().isoformat()
    url = build_sports_page_url(query, as_of_kst=as_of_kst)
    body = await fetch_page(url)
    if _looks_like_fetch_failure(body) or len(body.strip()) < _MIN_PAGE_CHARS:
        return [], ["날짜가 지정된 네이버 경기정보 페이지를 읽지 못했습니다."]
    fact = parse_naver_kbo_game_card(
        body,
        source_url=url,
        expected_date=expected_date,
        expected_team=expected_team,
    )
    if fact is None:
        return [], [
            "현재 기준일과 일치하는 경기정보 카드에서 팀·점수·상태를 모두 확인하지 못했습니다."
        ]
    text = (
        f"{fact.event_date} {fact.away_team} {fact.away_score} : "
        f"{fact.home_score} {fact.home_team} "
        f"{'final score' if fact.status == 'final' else 'live'}"
    )
    return [
        SourceDocument(
            source=fact.source,
            url=url,
            text=text,
            source_kind="sports_page",
            event_date=expected_date,
            sports_fact=fact,
        )
    ], []

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
) -> tuple[list[SourceDocument], list[str]]:
    """도메인별 source policy에 따라 검증된 원문 source만 반환한다."""
    if kind == "sports":
        return await _collect_sports_source(
            query=query,
            as_of_kst=as_of_kst,
            fetch_page=fetch_page,
        )
    if kind in {"news", "general", "market"}:
        return await _collect_news_sources(
            query=query,
            as_of_kst=as_of_kst,
            fetch_page=fetch_page,
        )
    return await _collect_direct_search_source(
        query=query,
        kind=kind,
        fetch_page=fetch_page,
    )


__all__ = [
    "FetchPage",
    "NewsCandidate",
    "SourceDocument",
    "SportsGameFact",
    "build_google_news_rss_url",
    "build_naver_search_url",
    "build_sports_page_url",
    "canonical_kbo_team",
    "collect_sources",
    "filter_recent_candidates",
    "html_to_visible_text",
    "parse_google_news_rss",
    "parse_naver_kbo_game_card",
]
