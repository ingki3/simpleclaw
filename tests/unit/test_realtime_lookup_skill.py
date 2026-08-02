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
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simpleclaw.skills import realtime_lookup
from simpleclaw.skills.realtime_sources import (
    CollectionOutcome,
    SourceDocument,
    SportsGameFact,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"

# ----------------------------------------------------------------------
# raw query fallback parser
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    (
        "import simpleclaw.skills.realtime_lookup",
        "from simpleclaw.agent import AgentOrchestrator",
    ),
)
def test_clean_process_import_contract(statement):
    """공개 realtime/agent import는 clean process의 import 순서와 무관하다."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_ROOT)

    completed = subprocess.run(
        [sys.executable, "-c", statement],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_installed_wrapper_imports_in_clean_process(tmp_path):
    """설치 스크립트가 만든 runtime wrapper도 clean process에서 import된다."""
    from scripts.install_realtime_lookup_skill import install

    skill_dir = install(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(_SRC_ROOT), str(skill_dir)))

    completed = subprocess.run(
        [sys.executable, "-c", "import realtime_lookup_skill"],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


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
        realtime_lookup,
        "lookup",
        lambda payload: {
            "query": payload["query"],
            "confidence": "low",
            "facts": [],
            "timeline_validation": {"status": "no_evidence"},
            "limitations": ["network disabled in test"],
        },
    )

    rc = realtime_lookup.main(["오늘", "코스피", "지수", "마감"])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["query"] == "오늘 코스피 지수 마감"
    # lookup 실패 envelope가 아니라 정상 evidence 계약을 따른다.
    assert "timeline_validation" in output
    assert not any("base64" in lim.lower() for lim in output["limitations"])


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


def _document_source(text: str) -> SourceDocument:
    return SourceDocument(
        source="Example News",
        url="https://example.com/article",
        text=text,
        source_kind="news_article",
        title="검증 기사",
        published_at="2026-06-26T09:00:00+00:00",
    )


def _request_payload(
    query: str,
    *,
    domain: str = "news",
    intents: tuple[str, ...] = ("current_result",),
    as_of_kst: str = "2026-06-26T20:00:00+09:00",
    freshness_required: bool = True,
) -> dict:
    entities = (
        [
            {"kind": "league", "value": "KBO"},
            {"kind": "team", "value": "롯데 자이언츠"},
        ]
        if domain == "sports"
        else []
    )
    return {
        "query": query,
        "domain": domain,
        "intents": list(intents),
        "entities": entities,
        "reference_date": as_of_kst[:10],
        "required_claims": [],
        "as_of_kst": as_of_kst,
        "freshness_required": freshness_required,
    }


def test_lookup_includes_timeline_validation_and_freshness(monkeypatch):
    """lookup()은 timeline_validation, freshness, limitations를 포함한다."""
    async def fake_collect_sources(**_kwargs):
        return [_document_source("결승전은 내일 오후 8시 개최 예정입니다." * 5)], []

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )

    result = realtime_lookup.lookup(_request_payload("오늘 결승전 결과"))

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
    async def fake_collect_sources(**_kwargs):
        return [
            _document_source(
                "1차전은 종료됐습니다. 2차전은 내일 예정으로 남아 있습니다." * 5
            )
        ], []

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )

    result = realtime_lookup.lookup(_request_payload("시리즈 경기 결과"))

    assert result["timeline_validation"]["status"] == "current_pending"
    assert result["evidence"][0]["timeline_status"] == "current_pending"
    assert result["limitations"]


def test_lookup_non_timeline_query_keeps_evidence(monkeypatch):
    """비일정 질문은 timeline 검증을 참고용으로만 두고 evidence는 유지한다."""
    async def fake_collect_sources(**_kwargs):
        return [_document_source("서울의 오늘 날씨는 맑고 기온은 25도입니다." * 5)], []

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )

    result = realtime_lookup.lookup(
        _request_payload(
            "서울 날씨 어때?",
            domain="weather",
            intents=("current_weather",),
            freshness_required=False,
        )
    )

    assert result["timeline_validation"]["is_timeline_sensitive"] is False
    assert result["evidence"]
    assert result["confidence"] == "medium"


# ----------------------------------------------------------------------
# structured facts / usable evidence
# ----------------------------------------------------------------------


def test_lookup_multi_source_yields_high_confidence(monkeypatch):
    """검증된 원문 두 건은 structured source facts와 high confidence를 만든다."""
    async def fake_collect_sources(**_kwargs):
        first = _document_source("A 기사 본문 " * 50)
        second = SourceDocument(
            source="Other News",
            url="https://other.example/article",
            text="B 기사 본문 " * 50,
            source_kind="news_article",
            title="다른 검증 기사",
            published_at="2026-06-26T08:30:00+00:00",
        )
        return [first, second], []

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )

    result = realtime_lookup.lookup(
        _request_payload(
            "이정후 시즌 타율",
            intents=("season_stat",),
            freshness_required=False,
        )
    )

    assert len(result["evidence"]) == 2
    assert result["confidence"] == "high"
    assert [fact["type"] for fact in result["facts"]] == [
        "source_excerpt",
        "source_excerpt",
    ]
    assert realtime_lookup.has_usable_realtime_evidence(result) is True


def test_lookup_sports_score_fact_keeps_all_values_together(monkeypatch):
    """스포츠 팀·점수·상태·승패·일자는 한 sports_score fact에 함께 있어야 한다."""
    fact = SportsGameFact(
        league="KBO",
        event_date="2026-07-24",
        status="final",
        away_team="kt wiz",
        away_score=5,
        home_team="롯데 자이언츠",
        home_score=4,
        winner="kt wiz",
        source="Naver Sports Game Card",
        source_url="https://search.naver.com/dated-game",
    )

    async def fake_collect_sources(**_kwargs):
        return [
            SourceDocument(
                source=fact.source,
                url=fact.source_url,
                text="2026-07-24 kt wiz 5 : 4 롯데 자이언츠 경기종료 최종 final",
                source_kind="sports_page",
                event_date=fact.event_date,
                sports_fact=fact,
            )
        ], []

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )
    result = realtime_lookup.lookup(
        _request_payload(
            "롯데 야구 어케 되었나?",
            domain="sports",
            as_of_kst="2026-07-24T22:18:43+09:00",
        )
    )

    assert result["facts"] == [{"type": "sports_score", **fact.__dict__}]
    assert result["timeline_validation"]["status"] == "final"
    assert result["confidence"] == "high"
    assert result["lookup_status"] == "found"


def _usable_sports_payload(*, status: str = "final") -> dict:
    winner = "롯데 자이언츠" if status == "final" else None
    return {
        "kind": "sports",
        "lookup_status": "found",
        "confidence": "high" if status == "final" else "medium",
        "facts": [
            {
                "type": "sports_score",
                "league": "KBO",
                "event_date": "2026-07-28",
                "status": status,
                "away_team": "롯데 자이언츠",
                "away_score": 8,
                "home_team": "한화 이글스",
                "home_score": 3,
                "winner": winner,
                "source": "Naver Sports Schedule API",
                "source_url": "https://api-gw.sports.naver.com/schedule/games",
            }
        ],
        "timeline_validation": {
            "status": "final" if status == "final" else "partial"
        },
    }


@pytest.mark.parametrize(
    ("status", "required_claims"),
    [("final", ["점수", "승패"]), ("live", ["현재 점수"])],
)
def test_typed_current_sports_fact_is_usable(status, required_claims):
    request = _request_payload(
        "롯데 야구 결과",
        domain="sports",
        as_of_kst="2026-07-29T08:00:00+09:00",
    )
    request["reference_date"] = "2026-07-28"
    request["required_claims"] = required_claims

    assert realtime_lookup.is_usable_realtime_evidence(
        _usable_sports_payload(status=status),
        request,
    )


@pytest.mark.parametrize(
    ("required_claims", "fact_update"),
    [
        (["승자"], {"winner": None}),
        (["관중 수"], {}),
        (["점수", "관중 수"], {}),
    ],
)
def test_missing_or_unsupported_sports_claim_is_unusable(
    required_claims,
    fact_update,
):
    payload = _usable_sports_payload()
    payload["facts"][0].update(fact_update)
    request = _request_payload(
        "롯데 야구 결과",
        domain="sports",
        as_of_kst="2026-07-29T08:00:00+09:00",
    )
    request.update(
        reference_date="2026-07-28",
        required_claims=required_claims,
    )

    assert not realtime_lookup.is_usable_realtime_evidence(payload, request)


def test_final_draw_satisfies_outcome_claim() -> None:
    payload = _usable_sports_payload()
    payload["facts"][0].update(
        away_score=3,
        home_score=3,
        winner=None,
    )
    request = _request_payload(
        "롯데 야구 결과",
        domain="sports",
        as_of_kst="2026-07-29T08:00:00+09:00",
    )
    request.update(reference_date="2026-07-28", required_claims=["승패"])

    assert realtime_lookup.is_usable_realtime_evidence(payload, request)


@pytest.mark.parametrize(
    ("payload_update", "fact_update", "request_update"),
    [
        ({"confidence": "low"}, {}, {}),
        ({"timeline_validation": {"status": "stale_or_pre_event"}}, {}, {}),
        ({}, {"event_date": "2026-07-27"}, {}),
        (
            {"timeline_validation": {"status": "partial"}},
            {"status": "scheduled", "winner": None},
            {},
        ),
        ({}, {}, {"required_claims": []}),
    ],
)
def test_stale_partial_or_incomplete_sports_fact_is_unusable(
    payload_update,
    fact_update,
    request_update,
):
    payload = _usable_sports_payload()
    payload.update(payload_update)
    payload["facts"][0].update(fact_update)
    request = _request_payload(
        "롯데 야구 결과",
        domain="sports",
        as_of_kst="2026-07-29T08:00:00+09:00",
    )
    request.update(
        reference_date="2026-07-28",
        required_claims=["점수", "승패"],
    )
    request.update(request_update)

    assert not realtime_lookup.is_usable_realtime_evidence(payload, request)


@pytest.mark.parametrize(
    ("fact_status", "source_text", "expected_timeline"),
    [
        ("live", "경기종료 final 승리", "partial"),
        ("final", "LIVE 경기중 7회말", "final"),
    ],
)
def test_sports_timeline_uses_structured_fact_status(
    monkeypatch, fact_status, source_text, expected_timeline
):
    """스포츠 timeline은 합성/표시 텍스트 cue가 아니라 enum 기반 fact를 따른다."""
    fact = SportsGameFact(
        league="KBO",
        event_date="2026-07-28",
        status=fact_status,
        away_team="롯데 자이언츠",
        away_score=8,
        home_team="한화 이글스",
        home_score=3,
        winner="롯데 자이언츠" if fact_status == "final" else None,
        source="Naver Sports Schedule API",
        source_url="https://api-gw.sports.naver.com/schedule/games",
    )
    document = SourceDocument(
        source=fact.source,
        url=fact.source_url,
        text=source_text,
        source_kind="sports_api",
        event_date=fact.event_date,
        sports_fact=fact,
    )

    async def fake_collect_sources(**_kwargs):
        return CollectionOutcome("found", [document], [])

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )
    result = realtime_lookup.lookup(
        _request_payload(
            "롯데 야구 결과",
            domain="sports",
            as_of_kst="2026-07-28T20:18:43+09:00",
        )
    )

    assert result["timeline_validation"]["status"] == expected_timeline
    assert result["evidence"][0]["timeline_status"] == expected_timeline


@pytest.mark.parametrize("collection_status", ["not_found", "failed"])
def test_lookup_propagates_typed_empty_sports_outcome(monkeypatch, collection_status):
    """명시적 no-match만 not_found이며 schema/fetch 실패는 failed로 유지한다."""
    async def fake_collect_sources(**_kwargs):
        return CollectionOutcome(collection_status, [], ["bounded fixture"])

    monkeypatch.setattr(
        realtime_lookup, "collect_sources_for_request", fake_collect_sources
    )
    result = realtime_lookup.lookup(
        _request_payload(
            "롯데 야구 결과",
            domain="sports",
            as_of_kst="2026-07-28T20:18:43+09:00",
        )
    )

    assert result["lookup_status"] == collection_status
    assert result["facts"] == []


@pytest.mark.parametrize(
    "result",
    [
        {"confidence": "low", "facts": [{"type": "sports_score"}]},
        {"confidence": "medium", "facts": []},
        {"confidence": "high", "facts": []},
        "not-json",
    ],
)
def test_low_or_empty_structured_evidence_is_not_usable(result):
    assert realtime_lookup.has_usable_realtime_evidence(result) is False


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
