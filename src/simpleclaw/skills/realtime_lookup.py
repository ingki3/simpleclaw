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

import asyncio
import base64
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen

from simpleclaw.skills.realtime_contracts import (
    LookupStatus,
    RealtimeLookupRequest,
    RealtimeLookupResult,
)
from simpleclaw.skills.realtime_sources import (
    CollectionOutcome,
    FetchPage,
    ResolveNewsUrl,
    SourceDocument,
    collect_sources_for_request,
    html_to_visible_text,
)

_SEARCH_TIMEOUT_SECONDS = 8
# SERP chrome 한 덩어리를 통째로 쓰던 시절의 보수적 cap. 폴백 경로에서만 쓴다.
# 결과 기사 본문을 직접 추출할 때 허용하는 소스별 최대 길이.
_MAX_SOURCE_CHARS = 2200
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


def _html_to_text(body: str, limit: int = _MAX_SOURCE_CHARS) -> str:
    """HTML에서 스크립트/페이지 chrome/태그를 제거해 본문 위주 텍스트로 축약한다.

    SERP·기사 페이지 모두 내비/헤더/푸터/사이드/폼 영역이 "관련 검색어", 메뉴,
    로그인 안내 같은 노이즈를 잔뜩 담는다. 이런 chrome 블록을 먼저 제거해 실제
    기사·데이터 텍스트 비중을 높인 뒤 태그를 벗긴다.
    """
    return html_to_visible_text(body, limit=limit)


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


async def _default_fetch_page(url: str) -> str:
    """CLI wrapper용 stdlib fetch callback (production은 내장 web_fetch를 주입)."""
    body, _limitations = await asyncio.to_thread(_fetch_raw, url)
    return body


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


def _fact_from_source(source: SourceDocument) -> dict[str, Any]:
    """검증된 source를 최종 모델이 판별 가능한 구조화 fact로 변환한다."""
    if source.sports_fact is not None:
        return {
            "type": "sports_score",
            **asdict(source.sports_fact),
        }
    # RSS metadata/title만 fact로 만들지 않는다. 최소 길이 검증을 통과해 실제로
    # fetch한 publisher 원문 발췌만 claim으로 노출한다.
    return {
        "type": "source_excerpt",
        "claim": source.text[:600],
        "source": source.source,
        "source_url": source.url,
        "published_at": source.published_at,
    }


def _parsed_result(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _request_value(
    request: RealtimeLookupRequest | Mapping[str, object] | None,
    name: str,
) -> object | None:
    if isinstance(request, RealtimeLookupRequest):
        return getattr(request, name, None)
    if isinstance(request, Mapping):
        return request.get(name)
    return None


def _expected_event_date(
    result: dict[str, Any],
    request: RealtimeLookupRequest | Mapping[str, object] | None,
) -> str | None:
    raw = _request_value(request, "reference_date")
    if raw:
        try:
            return datetime.fromisoformat(str(raw)).date().isoformat()
        except ValueError:
            return None
    raw = _request_value(request, "as_of_kst")
    freshness = result.get("freshness")
    if raw is None and isinstance(freshness, dict):
        raw = freshness.get("as_of_kst")
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date().isoformat()
    except ValueError:
        return None


_SPORTS_CLAIM_CAPABILITIES = {
    "점수": ("score",),
    "스코어": ("score",),
    "현재점수": ("score",),
    "현재스코어": ("score",),
    "최종점수": ("final_score",),
    "최종스코어": ("final_score",),
    "score": ("score",),
    "currentscore": ("score",),
    "finalscore": ("final_score",),
    "승패": ("outcome",),
    "승자": ("outcome",),
    "경기결과": ("outcome",),
    "최종결과": ("outcome",),
    "winner": ("outcome",),
    "outcome": ("outcome",),
    "result": ("outcome",),
    "finalresult": ("outcome",),
    "경기상태": ("status",),
    "진행상태": ("status",),
    "status": ("status",),
    "gamestatus": ("status",),
    "팀": ("teams",),
    "대진": ("teams",),
    "경기팀": ("teams",),
    "teams": ("teams",),
    "matchup": ("teams",),
    "경기일": ("event_date",),
    "경기날짜": ("event_date",),
    "날짜": ("event_date",),
    "eventdate": ("event_date",),
    "gamedate": ("event_date",),
    "리그": ("league",),
    "대회": ("league",),
    "league": ("league",),
}


def _normalized_claim(claim: object) -> str:
    """Planner claim을 명시적 capability key와 대조할 안정된 형태로 만든다."""
    return "".join(character for character in str(claim).casefold() if character.isalnum())


def _sports_claims_are_satisfied(
    required_claims: list[object] | tuple[object, ...],
    fact: Mapping[str, object],
) -> bool:
    """모든 sports claim이 실제 typed fact capability로 충족되는지 판정한다."""
    status = fact["status"]
    away_team = fact["away_team"]
    home_team = fact["home_team"]
    winner = fact["winner"]
    is_draw = (
        status == "final"
        and winner is None
        and fact["away_score"] == fact["home_score"]
    )
    capabilities = {
        "score": True,
        "final_score": status == "final",
        "outcome": status == "final"
        and (winner in {away_team, home_team} or is_draw),
        "status": status in {"final", "live"},
        "teams": bool(away_team and home_team),
        "event_date": bool(fact["event_date"]),
        "league": bool(fact["league"]),
    }
    for claim in required_claims:
        required = _SPORTS_CLAIM_CAPABILITIES.get(_normalized_claim(claim))
        if required is None or not all(capabilities[item] for item in required):
            return False
    return True


def is_usable_realtime_evidence(
    result: object,
    request: RealtimeLookupRequest | Mapping[str, object] | None = None,
) -> bool:
    """live-fact final을 허용할 수 있는 구조화 realtime result인지 판정한다."""
    parsed = _parsed_result(result)
    if parsed is None or parsed.get("confidence") not in {"medium", "high"}:
        return False
    if parsed.get("lookup_status") != LookupStatus.FOUND.value:
        return False
    facts = parsed.get("facts")
    if not isinstance(facts, list) or not facts or not all(
        isinstance(fact, dict) for fact in facts
    ):
        return False

    domain = str(_request_value(request, "domain") or parsed.get("kind") or "")
    if not domain or parsed.get("kind") != domain:
        return False
    required_claims = _request_value(request, "required_claims")
    if required_claims is not None and (
        not isinstance(required_claims, list | tuple)
        or not required_claims
        or any(not str(claim).strip() for claim in required_claims)
    ):
        return False
    freshness_required = bool(
        _request_value(request, "freshness_required")
    )
    timeline = parsed.get("timeline_validation")
    if timeline is not None and not isinstance(timeline, dict):
        return False
    if (
        (freshness_required or domain == "sports")
        and isinstance(timeline, dict)
        and timeline.get("status") in {
            "stale_or_pre_event",
            "current_pending",
            "no_evidence",
            "unknown",
        }
    ):
        return False
    if freshness_required and domain != "sports" and (
        not isinstance(timeline, dict) or timeline.get("status") != "final"
    ):
        return False
    if domain == "sports" and not isinstance(timeline, dict):
        return False

    # source 일부 실패 limitation은 허용한다. confidence가 low인 실패 envelope는
    # 위에서 이미 차단하므로 raw context bool로 판정하지 않는다.
    if domain != "sports":
        return all(
            str(fact.get("claim") or "").strip()
            and str(fact.get("source_url") or fact.get("url") or "").strip()
            for fact in facts
        )

    sports_facts = [fact for fact in facts if fact.get("type") == "sports_score"]
    if len(sports_facts) != 1 or len(facts) != 1:
        return False
    fact = sports_facts[0]
    required = (
        "league",
        "event_date",
        "away_team",
        "away_score",
        "home_team",
        "home_score",
        "status",
        "winner",
        "source",
        "source_url",
    )
    if any(field not in fact for field in required):
        return False
    if not all(fact[field] for field in ("league", "away_team", "home_team", "source", "source_url")):
        return False
    if not isinstance(fact["away_score"], int) or not isinstance(fact["home_score"], int):
        return False
    if fact["status"] not in {"final", "live"}:
        return False
    if fact["status"] == "live" and fact["winner"] is not None:
        return False
    if fact["status"] == "final":
        winner_is_team = fact["winner"] in {
            fact["away_team"],
            fact["home_team"],
        }
        is_draw = (
            fact["winner"] is None
            and fact["away_score"] == fact["home_score"]
        )
        if not winner_is_team and not is_draw:
            return False
    if required_claims is not None and not _sports_claims_are_satisfied(
        required_claims,
        fact,
    ):
        return False
    entities = _request_value(request, "entities")
    if isinstance(entities, (list, tuple)):
        for entity in entities:
            if isinstance(entity, Mapping):
                kind = str(entity.get("kind") or "")
                value = str(entity.get("value") or "")
            else:
                kind = str(getattr(entity, "kind", "") or "")
                value = str(getattr(entity, "value", "") or "")
            if kind == "league" and value.casefold() != str(fact["league"]).casefold():
                return False
            if kind in {"team", "athlete"} and value.casefold() not in {
                str(fact["away_team"]).casefold(),
                str(fact["home_team"]).casefold(),
            }:
                return False
    expected_date = _expected_event_date(parsed, request)
    if expected_date is None or fact["event_date"] != expected_date:
        return False
    expected_timeline = "final" if fact["status"] == "final" else "partial"
    return isinstance(timeline, dict) and timeline.get("status") == expected_timeline


# 이전 내부 import를 깨지 않되 production은 명시적인 이름을 사용한다.
has_usable_realtime_evidence = is_usable_realtime_evidence


async def lookup_async(
    payload: dict[str, Any] | RealtimeLookupRequest,
    fetch_page: FetchPage,
    resolve_news_url: ResolveNewsUrl | None = None,
) -> dict[str, Any] | RealtimeLookupResult:
    """실시간 조회를 실행하고 구조화된 evidence JSON dict를 반환한다.

    뉴스/일반은 Google News RSS로 후보를 찾은 뒤 원문을 읽고, 스포츠는 기준일로
    제한한 네이버 schedule API에서 한 경기의 enum/schema 값만 구조화한다.
    production에서는 오케스트레이터가 SSRF/redirect 정책을 적용한
    ``fetch_page``를 주입한다.
    """
    typed_input = isinstance(payload, RealtimeLookupRequest)
    try:
        request = (
            payload
            if typed_input
            else RealtimeLookupRequest.from_payload(payload)
        )
    except (TypeError, ValueError) as exc:
        raw = payload if isinstance(payload, dict) else {}
        fallback_request = RealtimeLookupRequest(
            query=str(raw.get("query") or ""),
            domain=str(raw.get("domain") or "unsupported"),
            intents=(),
            entities=(),
            reference_date="",
            required_claims=(),
            as_of_kst=str(raw.get("as_of_kst") or ""),
        )
        output = {
            "kind": fallback_request.domain,
            "query": fallback_request.query,
            "lookup_status": LookupStatus.UNSUPPORTED.value,
            "freshness": {"as_of_kst": fallback_request.as_of_kst},
            "confidence": "low",
            "evidence": [],
            "facts": [],
            "timeline_validation": {
                "is_timeline_sensitive": False,
                "status": "no_evidence",
                "as_of_kst": fallback_request.as_of_kst,
                "signals": {},
                "notes": [],
            },
            "limitations": [
                f"invalid structured realtime request: {type(exc).__name__}"
            ],
        }
        result = RealtimeLookupResult(
            request=fallback_request,
            status=LookupStatus.UNSUPPORTED,
            evidence=(),
            facts=(),
            limitations=tuple(output["limitations"]),
            payload=output,
        )
        return result if typed_input else output

    query = request.query
    kind = request.domain
    as_of_kst = request.as_of_kst
    collected = await collect_sources_for_request(
        request=request,
        fetch_page=fetch_page,
        resolve_news_url=resolve_news_url,
    )
    if isinstance(collected, CollectionOutcome):
        sources = collected.sources
        limitations = collected.limitations
        lookup_status = collected.lookup_status
    else:
        # 테스트/외부 monkeypatch의 기존 2-tuple 계약을 한 릴리스 동안 허용한다.
        sources, limitations = collected
        lookup_status = "found" if sources else "failed"
    limitations = list(limitations)
    now_utc = datetime.now(UTC).isoformat()

    combined_text = "\n".join(source.text for source in sources)

    # BIZ-383: 일정/상태성 질문이면 출처 본문이 어느 이벤트까지 반영했는지 검증한다.
    is_sensitive = bool(
        request.freshness_required
        or kind == "sports"
        or {
            "current_result",
            "current_status",
            "schedule",
            "forecast",
        }
        & set(request.intents)
    )
    sports_facts = [
        _fact_from_source(source)
        for source in sources
        if source.sports_fact is not None
    ]
    if kind == "sports" and sports_facts:
        sports_status = sports_facts[0]["status"]
        timeline_status = "final" if sports_status == "final" else "partial"
        timeline_validation = {
            "is_timeline_sensitive": True,
            "status": timeline_status,
            "as_of_kst": as_of_kst,
            "signals": {
                "future_cues": [],
                "past_cues": [],
                "in_progress_cues": [],
                "dates": [],
            },
            "notes": _timeline_notes(timeline_status, True),
        }
    else:
        timeline_validation = validate_timeline(
            combined_text, is_sensitive, as_of_kst
        )
    status = timeline_validation["status"]

    evidence = []
    for source in sources:
        # 출처별로 시간 cue를 따로 분류해, 어떤 소스가 stale/partial인지 드러낸다.
        if source.sports_fact is not None:
            source_status = (
                "final" if source.sports_fact.status == "final" else "partial"
            )
        else:
            source_signals = extract_time_signals(source.text)
            source_status = classify_timeline_status(source_signals, has_text=True)
        structured_fact = _fact_from_source(source)
        entry = {
            "source": source.source,
            "url": source.url,
            "source_url": source.url,
            "retrieved_at_utc": now_utc,
            "snippet": source.text,
            "source_kind": source.source_kind,
            "published_at": source.published_at,
            "event_date": source.event_date,
            "timeline_status": source_status,
            "structured_fact": structured_fact,
        }
        if structured_fact.get("type") == "sports_score":
            # 스포츠 evidence와 fact 양쪽에 동일 카드의 전체 필드를 평탄화한다.
            entry.update(structured_fact)
        evidence.append(entry)

    facts = [_fact_from_source(source) for source in sources]

    # confidence는 단순 URL 개수가 아니라 검증된 structured fact와 freshness를
    # 기준으로 산정한다. 완결된 sports_score는 단일 페이지여도 high다.
    sports_facts = [fact for fact in facts if fact.get("type") == "sports_score"]
    if not facts:
        confidence = "low"
    elif sports_facts and sports_facts[0].get("status") == "final":
        confidence = "high"
    elif sports_facts:
        confidence = "medium"
    elif not limitations and len(facts) >= 2:
        confidence = "high"
    else:
        # 일부 후보 fetch 실패 limitation이 있어도 검증된 원문 한 건은 medium이다.
        confidence = "medium"

    # 일정/상태성 질문에서 출처가 미확정/부분 반영이면 confidence를 보수적으로 낮추고
    # 한계를 명시해 최종 답변이 stale 전망을 확정처럼 말하지 않도록 한다.
    if is_sensitive and combined_text:
        if status in ("stale_or_pre_event", "no_evidence", "unknown"):
            confidence = "low"
            limitations.append(timeline_validation["notes"][0])
        elif status in ("current_pending", "partial"):
            is_live_sports = bool(
                sports_facts and sports_facts[0].get("status") == "live"
            )
            if not is_live_sports:
                confidence = "low"
            limitations.append(timeline_validation["notes"][0])

    output = {
        "kind": kind,
        "query": query,
        "lookup_status": lookup_status,
        "authoritative_empty": lookup_status == "not_found",
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
    try:
        typed_status = LookupStatus(lookup_status)
    except ValueError:
        typed_status = LookupStatus.FAILED
    result = RealtimeLookupResult(
        request=request,
        status=typed_status,
        evidence=tuple(evidence),
        facts=tuple(facts),
        limitations=tuple(limitations),
        payload=output,
    )
    return result if typed_input else output


def lookup(
    payload: dict[str, Any] | RealtimeLookupRequest,
    *,
    fetch_page: FetchPage = _default_fetch_page,
) -> dict[str, Any] | RealtimeLookupResult:
    """동기 CLI/테스트 호환 wrapper. async runtime은 ``lookup_async``를 사용한다."""
    return asyncio.run(lookup_async(payload, fetch_page=fetch_page))


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: base64 payload를 받아 JSON evidence를 stdout으로 출력한다."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            json.dumps(
                {
                    "kind": "unknown",
                    "lookup_status": "failed",
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
        if isinstance(result, RealtimeLookupResult):
            result = result.to_payload()
    except Exception as exc:  # noqa: BLE001 — stdout JSON contract 유지
        result = {
            "kind": "unknown",
            "lookup_status": "failed",
            "confidence": "low",
            "evidence": [],
            "facts": [],
            "limitations": [f"lookup failed: {type(exc).__name__}: {str(exc)[:180]}"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
