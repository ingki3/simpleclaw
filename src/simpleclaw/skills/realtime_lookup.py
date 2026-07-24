"""실시간 조회 evidence 스킬의 공용 실행 로직.

이 모듈은 외부 스킬 wrapper가 호출할 수 있는 순수 Python entrypoint를 제공한다.
오케스트레이터는 Gemini tool-call history 중간에 synthetic functionCall을 넣지 않고,
이 스킬을 LLM 루프 밖에서 실행한 뒤 구조화된 evidence JSON만 system context로
주입한다. 조회 품질은 도메인별 전용 스킬보다 보수적으로 설계하며, 실패 시에도
최종 답변이 한계를 명시할 수 있도록 limitations를 반환한다.

BIZ-383: 일정/상태성 질문에서 검색 결과 본문이 "어느 이벤트까지 반영했는지"를
검증하지 않으면 미래 전망(stale)·부분 결과(partial)·확정 결과(final)가 뒤섞여
답해지는 문제가 있었다. 그래서 출처 본문의 시간 cue(미래 예정/종료/진행 중)와
현재 기준시각을 비교해 ``timeline_validation`` 으로 확정/대기/부분반영/오래됨을
구분한다. 또한 raw query가 base64 토큰이 아닌 평문으로 직접 전달돼도 Unicode/
base64 오류 없이 query payload로 처리되도록 fallback parser를 둔다.
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

_SEARCH_TIMEOUT_SECONDS = 8
# SERP chrome 한 덩어리를 통째로 쓰던 시절의 보수적 cap. 폴백 경로에서만 쓴다.
_MAX_SNIPPET_CHARS = 1800
# 결과 기사 본문을 직접 추출할 때 허용하는 소스별 최대 길이.
_MAX_SOURCE_CHARS = 2200
# 한 질의에서 본문까지 회수할 최대 출처 수(교차검증용, 지연·타임아웃 균형).
_MAX_SOURCES = 2
# 읽을 만한 본문으로 인정하는 최소 길이(이 미만이면 chrome/차단 페이지로 간주).
_MIN_READABLE_CHARS = 120
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def decode_payload(token: str) -> dict[str, Any]:
    """단일 base64url 토큰 payload를 JSON dict로 복원한다.

    Args:
        token: 오케스트레이터가 전달한 URL-safe base64 encoded JSON.

    Returns:
        query/as_of_kst/prior_context 필드를 포함할 수 있는 dict.
    """
    padding = "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode((token + padding).encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def parse_args(args: list[str]) -> dict[str, Any]:
    """CLI 위치 인자를 payload dict로 복원한다(raw query fallback 포함).

    오케스트레이터는 URL-safe base64 단일 토큰을 넘기지만, 운영자나 다른 도구가
    스킬을 직접 호출할 때는 한국어 평문 질의를 그대로 전달할 수 있다. 이때 base64
    decode/JSON 파싱은 ``UnicodeDecodeError`` 나 ``binascii.Error`` 로 실패하므로,
    그런 경우 전체 인자를 공백으로 합쳐 raw query payload로 보수적으로 처리한다.

    Args:
        args: 스킬 executor가 공백 split해 전달한 위치 인자 목록.

    Returns:
        최소 ``query`` 를 가질 수 있는 payload dict (빈 인자면 빈 dict).
    """
    if not args:
        return {}
    # 1순위: 오케스트레이터가 직렬화한 base64url JSON 토큰
    try:
        decoded = decode_payload(args[0])
        if decoded:
            return decoded
    except Exception:  # noqa: BLE001 — base64/JSON 실패는 raw query fallback으로 흡수
        pass
    # 2순위: base64 토큰이 아니면 평문 질의로 간주 (Unicode/base64 오류 방지)
    raw_query = " ".join(arg for arg in args if arg).strip()
    return {"query": raw_query} if raw_query else {}


# 도메인 분류 cue. BIZ-394: market 분류를 단순 주가/지수 키워드에서 "기업·시장
# 이벤트(상장/IPO/공모/기업가치 등)"까지 넓혀, 영향·전망 질문이 general 로 새지
# 않고 시장 도메인 검색 URL/본문 회수 경로를 타도록 구조적 cue 를 추가한다.
_WEATHER_TERMS = ("날씨", "기온", "강수", "미세먼지", "예보")
_MARKET_TERMS = (
    "주가",
    "주식",
    "코스피",
    "코스닥",
    "나스닥",
    "s&p",
    "환율",
    "증시",
    "시장",
    "상장",
    "ipo",
    "공모",
    "기업가치",
    "시총",
)
_SPORTS_TERMS = ("kbo", "프로야구", "야구", "축구", "스코어")
_NEWS_TERMS = ("뉴스", "속보", "기사", "최신 소식")


def classify_query(query: str) -> str:
    """질문 문자열을 조회 도메인으로 보수 분류한다."""
    lowered = query.lower()
    if any(term in lowered for term in _WEATHER_TERMS):
        return "weather"
    if any(term in lowered for term in _MARKET_TERMS):
        return "market"
    if any(term in lowered for term in _SPORTS_TERMS):
        return "sports"
    if any(term in lowered for term in _NEWS_TERMS):
        return "news"
    return "general"


def build_search_url(query: str, kind: str) -> str:
    """도메인에 맞는 보수적 검색 URL을 만든다."""
    where = "news" if kind == "news" else "nexearch"
    return f"https://search.naver.com/search.naver?where={where}&query={quote_plus(query)}"


def _html_to_text(body: str, limit: int = _MAX_SOURCE_CHARS) -> str:
    """HTML에서 스크립트/페이지 chrome/태그를 제거해 본문 위주 텍스트로 축약한다.

    SERP·기사 페이지 모두 내비/헤더/푸터/사이드/폼 영역이 "관련 검색어", 메뉴,
    로그인 안내 같은 노이즈를 잔뜩 담는다. 이런 chrome 블록을 먼저 제거해 실제
    기사·데이터 텍스트 비중을 높인 뒤 태그를 벗긴다.
    """
    body = re.sub(r"(?is)<script.*?</script>", " ", body)
    body = re.sub(r"(?is)<style.*?</style>", " ", body)
    body = re.sub(r"(?is)<(nav|header|footer|aside|form)\b.*?</\1>", " ", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit]


def _fetch_raw(url: str) -> tuple[str, list[str]]:
    """URL을 정적 GET으로 조회해 raw HTML과 limitation을 반환한다."""
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_SEARCH_TIMEOUT_SECONDS) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read(500_000).decode(charset, errors="replace"), []
    except Exception as exc:  # noqa: BLE001 — 스킬은 실패도 구조화 evidence로 반환
        return "", [f"fetch failed: {type(exc).__name__}: {str(exc)[:180]}"]


def fetch_text(url: str) -> tuple[str, list[str]]:
    """URL을 정적 fetch로 조회하고 본문 텍스트와 limitation을 반환한다."""
    body, limitations = _fetch_raw(url)
    if not body:
        return "", limitations
    text = _html_to_text(body)
    if len(text) < _MIN_READABLE_CHARS:
        return text, ["fetched page contained very little readable text"]
    return text, []


# 결과 링크는 DuckDuckGo HTML 엔드포인트로 발견한다. 네이버 SERP 정적 HTML에는
# 실제 결과 기사 링크가 JS 렌더링돼 들어있지 않아(자산/트래커 링크만 노출) 본문
# 추적이 불가능한 반면, DuckDuckGo HTML은 정적으로 실제 목적지 URL을 돌려준다.
_DUCKDUCKGO_ENDPOINT = "https://html.duckduckgo.com/html/"
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"', re.IGNORECASE
)
_STATIC_ASSET_SUFFIXES = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".xml")


def _source_label(url: str) -> str:
    """URL에서 사람이 읽을 출처 호스트 라벨을 만든다."""
    host = urlparse(url).netloc
    return re.sub(r"^www\.", "", host) or "Web"


def _decode_ddg_href(href: str) -> str:
    """DuckDuckGo redirect href(`/l/?uddg=...`)에서 실제 목적지 URL을 복원한다."""
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if query.get("uddg"):
        return query["uddg"][0]
    return href


def extract_result_links(serp_html: str, limit: int = _MAX_SOURCES) -> list[str]:
    """DuckDuckGo HTML 결과에서 실제 콘텐츠 출처 URL을 순서대로 추출한다.

    SERP를 통째로 텍스트화하면 메뉴·광고 chrome만 남으므로, 결과 링크를 따라가
    기사 본문을 직접 회수하기 위한 후보 URL을 모은다. 검색엔진 내부 링크·정적 자원
    링크는 제외하고, 중복은 순서를 보존하며 제거한다.
    """
    links: list[str] = []
    for raw in _DDG_RESULT_RE.findall(serp_html):
        url = _decode_ddg_href(raw)
        lowered = url.lower()
        if not lowered.startswith(("http://", "https://")):
            continue
        if "duckduckgo.com" in lowered:
            continue
        if urlparse(lowered).path.endswith(_STATIC_ASSET_SUFFIXES):
            continue
        if url not in links:
            links.append(url)
        if len(links) >= limit:
            break
    return links


def discover_result_links(
    query: str,
    limit: int = _MAX_SOURCES,
) -> tuple[list[str], list[str]]:
    """질의어로 DuckDuckGo HTML을 조회해 결과 링크 후보와 limitation을 반환한다."""
    url = f"{_DUCKDUCKGO_ENDPOINT}?q={quote_plus(query)}"
    body, limitations = _fetch_raw(url)
    if not body:
        return [], limitations
    return extract_result_links(body, limit=limit), []


def gather_evidence(
    query: str,
    kind: str,
    max_sources: int = _MAX_SOURCES,
) -> tuple[list[dict[str, Any]], list[str]]:
    """검색 결과 링크를 따라 여러 출처의 본문을 회수해 evidence 소스 목록을 만든다.

    1) SERP에서 결과 링크 후보를 뽑고, 2) 상위 N개의 실제 기사 본문을 추출한다.
    링크를 못 얻거나 본문 회수에 모두 실패하면 SERP 추출 텍스트로 폴백해 최소한
    기존 동작 수준은 보장한다.

    Returns:
        ``(sources, limitations)`` — ``sources`` 는 ``{source, url, text}`` dict 목록.
    """
    limitations: list[str] = []
    links, link_lims = discover_result_links(query, limit=max_sources)
    limitations.extend(link_lims)

    sources: list[dict[str, Any]] = []
    for url in links[:max_sources]:
        text, lims = fetch_text(url)
        if text and len(text) >= _MIN_READABLE_CHARS:
            sources.append({"source": _source_label(url), "url": url, "text": text})
        else:
            limitations.extend(lims)

    # 결과 링크에서 본문을 한 건도 못 얻으면 네이버 SERP 추출 텍스트로 폴백한다.
    if not sources:
        serp_url = build_search_url(query, kind)
        text, lims = fetch_text(serp_url)
        if text:
            sources.append({"source": "Naver Search", "url": serp_url, "text": text})
        else:
            limitations.extend(lims)

    return sources, limitations


# 질문 자체가 "일정/상태/결과의 시점"에 민감한지 판정하는 cue.
# 단순 사실/정의 질문과 달리, 출처가 어느 이벤트까지 반영했는지 검증이 필요하다.
_TIMELINE_QUERY_CUES = (
    "일정",
    "언제",
    "몇 시",
    "몇시",
    "스케줄",
    "개막",
    "폐막",
    "결과",
    "스코어",
    "순위",
    "날짜",
    "일자",
    "마감",
    "발표",
    "출시",
    "예정",
    "다음 경기",
    "다음경기",
    "오늘 경기",
    "경기 결과",
    "최종",
    "확정",
    "schedule",
    "when",
    "result",
    "score",
    "standings",
    "fixture",
    "deadline",
)

# 출처 본문이 "아직 일어나지 않은" 이벤트만 가리키는 미래/예정 cue.
_FUTURE_EVENT_CUES = (
    "예정",
    "예보",
    "전망",
    "예상",
    "앞두고",
    "앞둔",
    "다가오",
    "개막 예정",
    "출시 예정",
    "오픈 예정",
    "예매",
    "킥오프",
    "내일",
    "모레",
    "다음 주",
    "다음주",
    "이번 주말",
    "곧 ",
    "scheduled",
    "upcoming",
    "will be held",
    "to be held",
    "kickoff",
)

# 이미 끝난 이벤트를 가리키는 과거/완료 cue.
_PAST_EVENT_CUES = (
    "종료",
    "끝났",
    "마감됐",
    "마감했",
    "최종",
    "확정",
    "우승",
    "승리",
    "패배",
    "완료",
    "마무리",
    "발표했",
    "공개됐",
    "공개했",
    "기록했",
    "ended",
    "final score",
    "finished",
    "completed",
    "concluded",
    "won",
    "defeated",
)

# 결과가 "확정"됐다고 볼 수 있는 강한 cue (final vs partial 구분용).
_FINAL_CONFIRMATION_CUES = (
    "최종",
    "확정",
    "우승",
    "최종 결과",
    "공식 발표",
    "final",
    "official",
)

# 이벤트가 "현재 진행 중"임을 가리키는 cue (결과 미확정 → 대기).
_IN_PROGRESS_CUES = (
    "진행 중",
    "진행중",
    "현재 진행",
    "생중계",
    "라이브",
    "전반",
    "후반",
    "현재 스코어",
    "live",
    "ongoing",
    "in progress",
)

# 본문에서 흔한 날짜 표기를 보수적으로 추출하는 정규식.
# 한국어/숫자형(2026년 6월 27일, 6월 27일, 6/27, 2026-06-27)을 우선 대상으로 한다.
_DATE_PATTERNS = (
    re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"),
    re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일"),
    re.compile(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}"),
    re.compile(r"\b\d{1,2}[./]\d{1,2}\b"),
)


def is_timeline_sensitive_query(query: str) -> bool:
    """질문이 일정/상태/결과의 시점에 민감한지 보수적으로 판정한다."""
    lowered = query.lower()
    return any(cue.lower() in lowered for cue in _TIMELINE_QUERY_CUES)


def _matched_cues(text: str, cues: tuple[str, ...]) -> list[str]:
    """본문(소문자화)에서 매칭된 cue 목록을 중복 없이 반환한다."""
    lowered = text.lower()
    seen: list[str] = []
    for cue in cues:
        if cue.lower() in lowered and cue not in seen:
            seen.append(cue)
    return seen


def extract_time_signals(text: str) -> dict[str, list[str]]:
    """출처 본문에서 미래/과거/진행 cue와 날짜 표기를 추출한다.

    완전한 timestamp parser가 아니라, 한국어/영어 기사·검색 snippet에서 흔한
    상태 cue와 날짜 패턴만 보수적으로 모은다. 추출 결과는 timeline 분류와
    limitations 설명의 근거로 쓰인다.
    """
    dates: list[str] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.findall(text):
            normalized = re.sub(r"\s+", "", match)
            if normalized not in dates:
                dates.append(normalized)
    return {
        "future_cues": _matched_cues(text, _FUTURE_EVENT_CUES),
        "past_cues": _matched_cues(text, _PAST_EVENT_CUES),
        "in_progress_cues": _matched_cues(text, _IN_PROGRESS_CUES),
        "dates": dates,
    }


def classify_timeline_status(signals: dict[str, list[str]], has_text: bool) -> str:
    """시간 cue 조합을 확정/대기/부분반영/오래됨 상태로 분류한다.

    Returns:
        - ``no_evidence``: 검증할 본문이 없음
        - ``stale_or_pre_event``: 미래 일정만 언급(현재 기준 미확정)
        - ``current_pending``: 종료 이벤트 + 남은 일정/진행 중 혼재(부분 확정·대기)
        - ``final``: 종료 + 확정 cue (확정 결과)
        - ``partial``: 종료/진행 흔적은 있으나 확정 cue 부족(부분 반영)
        - ``unknown``: 시간 cue 없음
    """
    if not has_text:
        return "no_evidence"
    past = bool(signals["past_cues"])
    future = bool(signals["future_cues"])
    in_progress = bool(signals["in_progress_cues"])

    # 미래 일정과 (종료 이벤트 또는 진행 중)이 함께면 일부 확정·일부 대기 상태.
    if future and (past or in_progress):
        return "current_pending"
    # 미래 일정만 있으면 아직 일어나지 않은 전망 → 현재 답변 근거로는 오래됨/사전.
    if future and not past and not in_progress:
        return "stale_or_pre_event"
    # 진행 중 cue만 있으면 결과 미확정 → 부분 반영.
    if in_progress and not past:
        return "partial"
    if past:
        joined_past = " ".join(signals["past_cues"]).lower()
        has_final = any(cue.lower() in joined_past for cue in _FINAL_CONFIRMATION_CUES)
        return "final" if has_final else "partial"
    return "unknown"


def validate_timeline(
    text: str,
    is_sensitive: bool,
    as_of_kst: Any,
) -> dict[str, Any]:
    """출처 본문 시간 cue와 기준시각을 비교한 timeline validation dict를 만든다."""
    signals = extract_time_signals(text) if text else {
        "future_cues": [],
        "past_cues": [],
        "in_progress_cues": [],
        "dates": [],
    }
    status = classify_timeline_status(signals, has_text=bool(text))
    notes = _timeline_notes(status, is_sensitive)
    return {
        "is_timeline_sensitive": is_sensitive,
        "status": status,
        "as_of_kst": as_of_kst,
        "signals": signals,
        "notes": notes,
    }


def _timeline_notes(status: str, is_sensitive: bool) -> list[str]:
    """timeline 상태를 최종 답변이 쓸 수 있는 짧은 자연어 설명으로 변환한다."""
    if not is_sensitive:
        return ["질문이 일정/상태성으로 분류되지 않아 timeline 검증은 참고용입니다."]
    mapping = {
        "no_evidence": "검증할 출처 본문을 확보하지 못해 일정/상태를 확정할 수 없습니다.",
        "stale_or_pre_event": (
            "출처가 미래 일정/예정만 언급합니다. 현재 시점 확정 결과가 아니므로 "
            "전망으로만 답하고 확정 결과로 단정하지 마세요."
        ),
        "current_pending": (
            "일부 이벤트는 종료됐고 남은 일정/진행이 함께 있습니다. 확정된 부분과 "
            "대기 중인 부분을 구분해 답하세요."
        ),
        "partial": (
            "결과가 부분적으로만 반영됐을 수 있습니다. 확정 결과로 단정하지 말고 "
            "부분 반영 가능성을 명시하세요."
        ),
        "final": "출처가 확정 결과를 가리킵니다. 다만 기준시각과 출처 시각을 함께 밝혀 주세요.",
        "unknown": (
            "출처 본문에서 명확한 시간 cue를 찾지 못했습니다. 시점 확정성에 주의하세요."
        ),
    }
    return [mapping.get(status, mapping["unknown"])]


def lookup(payload: dict[str, Any]) -> dict[str, Any]:
    """실시간 조회를 실행하고 구조화된 evidence JSON dict를 반환한다.

    검색 품질을 위해 SERP chrome 한 덩어리 대신 결과 링크를 따라 여러 출처의 실제
    본문을 회수하고, 각 출처를 개별 evidence로 노출해 최종 답변이 교차검증할 수
    있게 한다. 본문 전체를 합쳐 timeline 검증과 confidence 산정에 쓴다.
    """
    query = str(payload.get("query") or "").strip() or "실시간 정보"
    kind = classify_query(query)
    sources, limitations = gather_evidence(query, kind)
    limitations = list(limitations)
    now_utc = datetime.now(UTC).isoformat()
    as_of_kst = payload.get("as_of_kst")

    combined_text = "\n".join(source["text"] for source in sources)

    # BIZ-383: 일정/상태성 질문이면 출처 본문이 어느 이벤트까지 반영했는지 검증한다.
    is_sensitive = is_timeline_sensitive_query(query)
    timeline_validation = validate_timeline(combined_text, is_sensitive, as_of_kst)
    status = timeline_validation["status"]

    evidence = []
    for source in sources:
        # 출처별로 시간 cue를 따로 분류해, 어떤 소스가 stale/partial인지 드러낸다.
        source_signals = extract_time_signals(source["text"])
        source_status = classify_timeline_status(source_signals, has_text=True)
        evidence.append(
            {
                "source": source["source"],
                "url": source["url"],
                "retrieved_at_utc": now_utc,
                "snippet": source["text"],
                "timeline_status": source_status,
            }
        )

    facts = []
    if sources:
        facts.append(
            {
                "claim": f"{len(sources)}개 출처에서 최신 정보 본문을 확보했습니다.",
                "source": ", ".join(source["source"] for source in sources),
            }
        )

    # 멀티소스 + 한계 없음이면 교차검증이 가능하므로 high, 단일 출처면 medium,
    # 회수 본문이 없으면 low. 이후 timeline 검증 결과로 한 번 더 보수 조정한다.
    if not combined_text:
        confidence = "low"
    elif not limitations and len(sources) >= 2:
        confidence = "high"
    elif not limitations:
        confidence = "medium"
    else:
        confidence = "low"

    # 일정/상태성 질문에서 출처가 미확정/부분 반영이면 confidence를 보수적으로 낮추고
    # 한계를 명시해 최종 답변이 stale 전망을 확정처럼 말하지 않도록 한다.
    if is_sensitive and combined_text:
        if status in ("stale_or_pre_event", "no_evidence", "unknown"):
            confidence = "low"
            limitations.append(timeline_validation["notes"][0])
        elif status in ("current_pending", "partial"):
            confidence = "low" if confidence in ("medium", "high") else confidence
            limitations.append(timeline_validation["notes"][0])

    return {
        "kind": kind,
        "query": query,
        "freshness": {
            "as_of_kst": as_of_kst,
            "retrieved_at_utc": now_utc,
            "timeline_status": status,
        },
        "confidence": confidence,
        "evidence": evidence,
        "facts": facts,
        "timeline_validation": timeline_validation,
        "limitations": limitations,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: base64 payload를 받아 JSON evidence를 stdout으로 출력한다."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            json.dumps(
                {
                    "kind": "unknown",
                    "confidence": "low",
                    "evidence": [],
                    "facts": [],
                    "limitations": ["missing payload"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    try:
        payload = parse_args(args)
        result = lookup(payload)
    except Exception as exc:  # noqa: BLE001 — stdout JSON contract 유지
        result = {
            "kind": "unknown",
            "confidence": "low",
            "evidence": [],
            "facts": [],
            "limitations": [f"lookup failed: {type(exc).__name__}: {str(exc)[:180]}"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
