"""BIZ-383 실시간 evidence 스킬의 raw query fallback과 timeline validation 테스트.

이 스킬은 오케스트레이터가 LLM 루프 밖에서 실행하는 내부 evidence 스킬이다.
- raw Korean args가 base64/Unicode 오류 없이 query payload로 처리되는지
- 출처 본문 시간 cue를 비교해 stale/pre-event·partial·current-pending·final로
  분류하는지
- ``lookup()`` 출력이 ``timeline_validation`` / ``freshness`` / ``limitations`` 를
  포함하는지
를 검증한다.
"""

from __future__ import annotations

import base64
import json

import pytest

from simpleclaw.skills import realtime_lookup

# ----------------------------------------------------------------------
# raw query fallback parser
# ----------------------------------------------------------------------


def test_parse_args_decodes_base64_payload():
    """오케스트레이터가 직렬화한 base64url JSON 토큰은 그대로 복원된다."""
    payload = {"query": "오늘 KBO 경기 결과", "as_of_kst": "2026-06-26T20:00:00+09:00"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii")

    assert realtime_lookup.parse_args([token]) == payload


def test_parse_args_falls_back_to_raw_korean_query():
    """base64 토큰이 아닌 한국어 평문 인자는 query payload로 처리된다(디코드 오류 없음)."""
    args = ["오늘", "프로야구", "경기", "일정"]

    parsed = realtime_lookup.parse_args(args)

    assert parsed == {"query": "오늘 프로야구 경기 일정"}


def test_parse_args_empty_returns_empty_dict():
    """인자가 없으면 빈 payload를 반환한다."""
    assert realtime_lookup.parse_args([]) == {}


def test_main_with_raw_korean_args_does_not_error(monkeypatch, capsys):
    """raw Korean args 직접 호출이 Unicode/base64 오류로 끝나지 않는다."""
    monkeypatch.setattr(
        realtime_lookup, "discover_result_links", lambda query, limit=2: ([], [])
    )
    monkeypatch.setattr(
        realtime_lookup, "fetch_text", lambda url: ("", ["network disabled in test"])
    )

    rc = realtime_lookup.main(["오늘", "코스피", "지수", "마감"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["query"] == "오늘 코스피 지수 마감"
    # lookup 실패 envelope가 아니라 정상 evidence 계약을 따른다.
    assert "timeline_validation" in output
    assert not any("base64" in lim.lower() for lim in output["limitations"])


# ----------------------------------------------------------------------
# domain classification (BIZ-394 구조적 market cue)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "OpenAI 상장 일정이 어떻게 돼?",
        "엔비디아 IPO 공모가 전망",
        "이번 인수로 기업가치가 얼마나 오를까",
        "증시 영향 분석해줘",
        "테슬라 시총 변화",
    ],
)
def test_classify_query_market_domain_events(query):
    """기업·시장 이벤트(상장/IPO/공모/기업가치/시총/증시)는 market 도메인으로 분류된다."""
    assert realtime_lookup.classify_query(query) == "market"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("오늘 서울 날씨 어때?", "weather"),
        ("코스피 지금 얼마야?", "market"),
        ("어제 KBO 경기 스코어", "sports"),
        ("최신 속보 알려줘", "news"),
        ("파이썬 리스트가 뭐야?", "general"),
    ],
)
def test_classify_query_domain_mapping(query, expected):
    """주요 도메인 분류가 회귀하지 않는다."""
    assert realtime_lookup.classify_query(query) == expected


# ----------------------------------------------------------------------
# timeline-sensitive query detector
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["오늘 경기 결과 알려줘", "다음 경기 일정", "코스피 마감 순위", "release schedule"],
)
def test_timeline_sensitive_queries_detected(query):
    """일정/상태/결과 질문은 timeline-sensitive로 분류된다."""
    assert realtime_lookup.is_timeline_sensitive_query(query) is True


@pytest.mark.parametrize("query", ["파이썬 리스트가 뭐야?", "맛집 추천해줘"])
def test_non_timeline_queries_not_detected(query):
    """일정/상태 cue가 없는 일반 질문은 timeline-sensitive가 아니다."""
    assert realtime_lookup.is_timeline_sensitive_query(query) is False


# ----------------------------------------------------------------------
# time-signal extraction + classification
# ----------------------------------------------------------------------


def test_future_only_source_is_stale_or_pre_event():
    """미래 일정만 언급한 출처는 stale_or_pre_event로 분류된다."""
    text = "결승전은 2026년 6월 28일에 열릴 예정입니다. 킥오프는 오후 8시 예정."

    result = realtime_lookup.validate_timeline(text, is_sensitive=True, as_of_kst=None)

    assert result["status"] == "stale_or_pre_event"
    assert result["signals"]["future_cues"]
    assert not result["signals"]["past_cues"]


def test_finished_and_remaining_source_is_current_pending():
    """끝난 이벤트와 남은 일정이 함께 있는 출처는 current_pending로 분류된다."""
    text = (
        "1차전은 3-1로 종료됐습니다. 2차전은 내일 오후 7시 예정으로 남아 있습니다."
    )

    result = realtime_lookup.validate_timeline(text, is_sensitive=True, as_of_kst=None)

    assert result["status"] == "current_pending"
    assert result["signals"]["past_cues"]
    assert result["signals"]["future_cues"]


def test_confirmed_result_source_is_final():
    """종료 + 확정 cue가 있는 출처는 final로 분류된다."""
    text = "경기가 종료됐고 최종 스코어는 4-2, 우승팀이 확정됐습니다."

    result = realtime_lookup.validate_timeline(text, is_sensitive=True, as_of_kst=None)

    assert result["status"] == "final"


def test_in_progress_source_is_partial():
    """진행 중 cue만 있으면 결과 미확정으로 partial로 분류된다."""
    text = "현재 후반전 진행 중이며 스코어는 1-1 입니다."

    result = realtime_lookup.validate_timeline(text, is_sensitive=True, as_of_kst=None)

    assert result["status"] == "partial"


def test_no_text_is_no_evidence():
    """검증할 본문이 없으면 no_evidence로 분류된다."""
    result = realtime_lookup.validate_timeline("", is_sensitive=True, as_of_kst=None)

    assert result["status"] == "no_evidence"


def test_extract_time_signals_collects_dates():
    """본문에서 흔한 날짜 표기를 보수적으로 추출한다."""
    signals = realtime_lookup.extract_time_signals(
        "2026년 6월 27일 경기, 다음은 7/1 예정"
    )

    assert "2026년6월27일" in signals["dates"]
    assert "7/1" in signals["dates"]


# ----------------------------------------------------------------------
# lookup() output contract
# ----------------------------------------------------------------------


def test_lookup_includes_timeline_validation_and_freshness(monkeypatch):
    """lookup()은 timeline_validation, freshness, limitations를 포함한다."""
    monkeypatch.setattr(realtime_lookup, "discover_result_links", lambda query, limit=2: ([], []))
    monkeypatch.setattr(
        realtime_lookup,
        "fetch_text",
        lambda url: ("결승전은 내일 오후 8시 개최 예정입니다." * 5, []),
    )

    result = realtime_lookup.lookup(
        {"query": "오늘 결승전 결과", "as_of_kst": "2026-06-26T20:00:00+09:00"}
    )

    assert "timeline_validation" in result
    assert result["timeline_validation"]["status"] == "stale_or_pre_event"
    assert result["timeline_validation"]["is_timeline_sensitive"] is True
    assert result["freshness"]["as_of_kst"] == "2026-06-26T20:00:00+09:00"
    assert result["freshness"]["timeline_status"] == "stale_or_pre_event"
    # 미래 일정만 가리키므로 confidence는 보수적으로 낮추고 한계를 명시한다.
    assert result["confidence"] == "low"
    assert result["limitations"]


def test_lookup_marks_pending_source_with_limitation(monkeypatch):
    """current_pending 출처는 부분 확정 한계를 limitations로 명시한다."""
    monkeypatch.setattr(realtime_lookup, "discover_result_links", lambda query, limit=2: ([], []))
    monkeypatch.setattr(
        realtime_lookup,
        "fetch_text",
        lambda url: (
            "1차전은 종료됐습니다. 2차전은 내일 예정으로 남아 있습니다." * 5,
            [],
        ),
    )

    result = realtime_lookup.lookup({"query": "시리즈 경기 결과"})

    assert result["timeline_validation"]["status"] == "current_pending"
    assert result["evidence"][0]["timeline_status"] == "current_pending"
    assert result["limitations"]


def test_lookup_non_timeline_query_keeps_evidence(monkeypatch):
    """비일정 질문은 timeline 검증을 참고용으로만 두고 evidence는 유지한다."""
    monkeypatch.setattr(realtime_lookup, "discover_result_links", lambda query, limit=2: ([], []))
    monkeypatch.setattr(
        realtime_lookup,
        "fetch_text",
        lambda url: ("서울의 오늘 날씨는 맑고 기온은 25도입니다." * 5, []),
    )

    result = realtime_lookup.lookup({"query": "서울 날씨 어때?"})

    assert result["timeline_validation"]["is_timeline_sensitive"] is False
    assert result["evidence"]
    assert result["confidence"] == "medium"


# ----------------------------------------------------------------------
# multi-source 본문 추출 (검색 품질)
# ----------------------------------------------------------------------


def test_extract_result_links_decodes_ddg_and_filters_assets():
    """DuckDuckGo HTML 결과에서 redirect URL을 복원하고 자산/내부 링크는 제외한다."""
    ddg_html = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.a.com%2Fp1&amp;rut=x">기사1</a>
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcdn.example.com%2Fapp.js">script</a>
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.b.com%2Fp2&amp;rut=y">기사2</a>
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.a.com%2Fp1">중복</a>
    <a href="https://other.site/nav">non-result anchor</a>
    """

    links = realtime_lookup.extract_result_links(ddg_html, limit=5)

    assert "https://news.a.com/p1" in links
    assert "https://news.b.com/p2" in links
    # 정적 자산(.js)·result__a 아닌 내비 앵커는 제외.
    assert "https://cdn.example.com/app.js" not in links
    assert "https://other.site/nav" not in links
    # 중복은 순서를 보존하며 한 번만 남는다.
    assert links.count("https://news.a.com/p1") == 1


def test_gather_evidence_follows_links_to_article_bodies(monkeypatch):
    """결과 링크를 따라 각 기사 본문을 개별 출처로 회수한다."""
    monkeypatch.setattr(
        realtime_lookup,
        "discover_result_links",
        lambda query, limit=2: (
            ["https://news.a.com/p1", "https://news.b.com/p2"],
            [],
        ),
    )
    bodies = {
        "https://news.a.com/p1": ("A 기사 본문 " * 30, []),
        "https://news.b.com/p2": ("B 기사 본문 " * 30, []),
    }
    monkeypatch.setattr(realtime_lookup, "fetch_text", lambda url: bodies[url])

    sources, limitations = realtime_lookup.gather_evidence("최신 뉴스", "news")

    assert [s["url"] for s in sources] == [
        "https://news.a.com/p1",
        "https://news.b.com/p2",
    ]
    assert sources[0]["source"] == "news.a.com"
    assert not limitations


def test_lookup_multi_source_yields_high_confidence(monkeypatch):
    """두 출처 본문을 확보하고 한계가 없으면 confidence가 high로 올라간다."""
    monkeypatch.setattr(
        realtime_lookup,
        "discover_result_links",
        lambda query, limit=2: (["https://a.com/x", "https://b.com/y"], []),
    )
    monkeypatch.setattr(
        realtime_lookup,
        "fetch_text",
        lambda url: ("이정후 타율 0.332 시즌 기록 정리. " * 20, []),
    )

    result = realtime_lookup.lookup({"query": "이정후 시즌 타율"})

    assert len(result["evidence"]) == 2
    assert result["confidence"] == "high"
    assert result["facts"][0]["source"] == "a.com, b.com"


def test_gather_evidence_falls_back_to_serp_text_when_no_links(monkeypatch):
    """결과 링크가 없으면 SERP 추출 텍스트로 폴백해 최소 동작을 보장한다."""
    monkeypatch.setattr(realtime_lookup, "discover_result_links", lambda query, limit=2: ([], []))
    monkeypatch.setattr(
        realtime_lookup, "fetch_text", lambda url: ("SERP 본문 텍스트 " * 20, [])
    )

    sources, _ = realtime_lookup.gather_evidence("오늘 뉴스", "news")

    assert len(sources) == 1
    assert sources[0]["source"] == "Naver Search"


def test_html_to_text_strips_chrome_blocks():
    """nav/header/footer 같은 chrome 블록은 본문 텍스트에서 제거된다."""
    html_body = (
        "<header>메뉴 로그인 관련검색어</header>"
        "<article>핵심 기사 본문 내용</article>"
        "<footer>저작권 약관 고객센터</footer>"
    )

    text = realtime_lookup._html_to_text(html_body)

    assert "핵심 기사 본문 내용" in text
    assert "메뉴 로그인" not in text
    assert "저작권 약관" not in text
