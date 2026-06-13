"""실시간 조회 evidence 스킬의 공용 실행 로직.

이 모듈은 외부 스킬 wrapper가 호출할 수 있는 순수 Python entrypoint를 제공한다.
오케스트레이터는 Gemini tool-call history 중간에 synthetic functionCall을 넣지 않고,
이 스킬을 LLM 루프 밖에서 실행한 뒤 구조화된 evidence JSON만 system context로
주입한다. 조회 품질은 도메인별 전용 스킬보다 보수적으로 설계하며, 실패 시에도
최종 답변이 한계를 명시할 수 있도록 limitations를 반환한다.
"""

from __future__ import annotations

import base64
import html
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

_SEARCH_TIMEOUT_SECONDS = 8
_MAX_SNIPPET_CHARS = 1800
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


def classify_query(query: str) -> str:
    """질문 문자열을 조회 도메인으로 보수 분류한다."""
    lowered = query.lower()
    if any(term in query for term in ("날씨", "기온", "강수", "미세먼지", "예보")):
        return "weather"
    if any(
        term in lowered
        for term in ("주가", "주식", "코스피", "코스닥", "나스닥", "s&p", "환율")
    ):
        return "market"
    if any(term in lowered for term in ("kbo", "프로야구", "야구", "축구", "스코어")):
        return "sports"
    if any(term in query for term in ("뉴스", "속보", "기사", "최신 소식")):
        return "news"
    return "general"


def build_search_url(query: str, kind: str) -> str:
    """도메인에 맞는 보수적 검색 URL을 만든다."""
    where = "news" if kind == "news" else "nexearch"
    return f"https://search.naver.com/search.naver?where={where}&query={quote_plus(query)}"


def _html_to_text(body: str) -> str:
    """검색 결과 HTML에서 스크립트/태그를 제거해 짧은 evidence 텍스트로 축약한다."""
    body = re.sub(r"(?is)<script.*?</script>", " ", body)
    body = re.sub(r"(?is)<style.*?</style>", " ", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:_MAX_SNIPPET_CHARS]


def fetch_text(url: str) -> tuple[str, list[str]]:
    """URL을 정적 fetch로 조회하고 evidence 텍스트와 limitation을 반환한다."""
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_SEARCH_TIMEOUT_SECONDS) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(500_000).decode(charset, errors="replace")
    except Exception as exc:  # noqa: BLE001 — 스킬은 실패도 구조화 evidence로 반환
        return "", [f"fetch failed: {type(exc).__name__}: {str(exc)[:180]}"]

    text = _html_to_text(body)
    if len(text) < 120:
        return text, ["fetched page contained very little readable text"]
    return text, []


def lookup(payload: dict[str, Any]) -> dict[str, Any]:
    """실시간 조회를 실행하고 구조화된 evidence JSON dict를 반환한다."""
    query = str(payload.get("query") or "").strip() or "실시간 정보"
    kind = classify_query(query)
    url = build_search_url(query, kind)
    text, limitations = fetch_text(url)
    confidence = "medium" if text and not limitations else "low"
    now_utc = datetime.now(timezone.utc).isoformat()
    evidence = []
    facts = []
    if text:
        evidence.append(
            {
                "source": "Naver Search",
                "url": url,
                "retrieved_at_utc": now_utc,
                "snippet": text,
            }
        )
        facts.append(
            {
                "claim": "검색 결과에서 확인 가능한 최신 정보 스니펫을 확보했습니다.",
                "source": "Naver Search",
            }
        )
    return {
        "kind": kind,
        "query": query,
        "freshness": {
            "as_of_kst": payload.get("as_of_kst"),
            "retrieved_at_utc": now_utc,
        },
        "confidence": confidence,
        "evidence": evidence,
        "facts": facts,
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
        payload = decode_payload(args[0])
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
