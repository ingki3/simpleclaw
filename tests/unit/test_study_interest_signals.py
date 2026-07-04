"""Dreaming/대화 기반 관심사 signal 추출 검증.

DoD:
- Dreaming 결과와 사용자의 organic 질문에서 topic hint 를 만든다.
- cron/recipe 자동 산출물이 사용자 관심사로 과대 반영되지 않는다.
- signal 마다 source/weight/confidence 가 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from simpleclaw.study.interest_signals import (
    AUTO_REPORT_MAX_WEIGHT,
    InterestSignal,
    derive_topic_hint,
    extract_keywords,
    extract_topic_hints,
    signals_from_auto_reports,
    signals_from_insights,
    signals_from_memory_items,
    signals_from_user_messages,
)


# --------------------------------------------------------------------------- #
# 계획서가 명시한 핵심 시나리오
# --------------------------------------------------------------------------- #


def test_extract_topic_hints_prioritizes_user_questions_over_auto_reports():
    signals = extract_topic_hints(
        user_messages=["OpenAI 상장 연기가 증시에 끼치는 영향 조사해줘"],
        memory_items=[{"type": "cluster_summary", "text": "AI 코딩 에이전트와 증시 질문이 반복됨"}],
        auto_reports=["일반 정치 뉴스 브리핑"],
    )

    hints = [s.topic_hint for s in signals]
    assert any("OpenAI" in h or "AI" in h for h in hints)
    assert all(s.weight < 0.5 for s in signals if s.source == "auto_report")


def test_user_question_outweighs_auto_report():
    """organic 질문이 자동 산출물보다 항상 높은 가중치를 갖는다(과대 일반화 방어)."""
    signals = extract_topic_hints(
        user_messages=["NVIDIA 실적 발표 정리해줘"],
        auto_reports=["오늘의 일반 뉴스 브리핑"],
    )
    user = next(s for s in signals if s.source == "user_message")
    auto = next(s for s in signals if s.source == "auto_report")
    assert user.weight > auto.weight
    assert auto.weight < AUTO_REPORT_MAX_WEIGHT


# --------------------------------------------------------------------------- #
# topic hint 추출
# --------------------------------------------------------------------------- #


def test_derive_topic_hint_strips_request_tail():
    assert derive_topic_hint("OpenAI 상장 연기 조사해줘") == "OpenAI 상장 연기"
    assert derive_topic_hint("NVIDIA 실적 알려줘") == "NVIDIA 실적"


def test_derive_topic_hint_strips_narrative_tail():
    hint = derive_topic_hint("AI 코딩 에이전트와 증시 질문이 반복됨")
    assert "AI 코딩 에이전트" in hint
    assert "반복" not in hint


def test_derive_topic_hint_preserves_proper_noun():
    assert "OpenAI" in derive_topic_hint("OpenAI 관련 뉴스 정리해줘")


def test_derive_topic_hint_empty_and_whitespace():
    assert derive_topic_hint("") == ""
    assert derive_topic_hint("   ") == ""


def test_derive_topic_hint_request_only_falls_back():
    # 꼬리만 있는 문장은 빈 결과 대신 원문 정리본으로 폴백한다.
    assert derive_topic_hint("알려줘") != ""


def test_extract_keywords_prioritizes_proper_nouns():
    keywords = extract_keywords("OpenAI 와 GPT 모델의 증시 영향")
    assert "OpenAI" in keywords
    assert "GPT" in keywords


# --------------------------------------------------------------------------- #
# user_messages — 반복 질문 가중치
# --------------------------------------------------------------------------- #


def test_repeated_user_question_gets_higher_weight():
    once = signals_from_user_messages(["테슬라 주가 분석해줘"])
    repeated = signals_from_user_messages(
        [
            "테슬라 주가 분석해줘",
            "테슬라 주가 어떻게 되고 있어 분석해줘",
        ]
    )
    assert len(repeated) == 1  # 같은 주제로 묶임
    assert repeated[0].weight > once[0].weight
    assert repeated[0].confidence > once[0].confidence
    assert "x2" in repeated[0].source_ref


def test_user_message_accepts_dict_with_text():
    signals = signals_from_user_messages([{"text": "쿠팡 실적 정리해줘"}])
    assert len(signals) == 1
    assert "쿠팡" in signals[0].topic_hint


def test_blank_user_messages_are_skipped():
    assert signals_from_user_messages(["", "   ", {"text": ""}]) == []


# --------------------------------------------------------------------------- #
# memory_items — 채택 type 과 가중치
# --------------------------------------------------------------------------- #


def test_memory_items_only_accept_recognized_types():
    signals = signals_from_memory_items(
        [
            {"type": "accepted_user_insight", "text": "사용자는 반도체 투자에 관심"},
            {"type": "active_project", "text": "사이드 프로젝트로 트레이딩 봇 개발"},
            {"type": "decision", "text": "맥북 프로 구매 결정"},
            {"type": "cluster_summary", "text": "AI 에이전트 논의"},
            {"type": "memory", "text": "이건 일반 메모라 제외"},
            {"type": "user", "text": "이름은 길동"},
        ]
    )
    sources = {s.source for s in signals}
    assert sources == {
        "accepted_user_insight",
        "active_project",
        "decision",
        "cluster_summary",
    }


def test_accepted_user_insight_outweighs_cluster_summary():
    signals = signals_from_memory_items(
        [
            {"type": "accepted_user_insight", "text": "반도체 투자에 강한 관심"},
            {"type": "cluster_summary", "text": "잡담성 클러스터 요약"},
        ]
    )
    by_source = {s.source: s for s in signals}
    assert by_source["accepted_user_insight"].weight > by_source["cluster_summary"].weight


def test_memory_item_confidence_scales_weight():
    high = signals_from_memory_items(
        [{"type": "cluster_summary", "text": "주제 A", "confidence": 0.9}]
    )[0]
    low = signals_from_memory_items(
        [{"type": "cluster_summary", "text": "주제 A", "confidence": 0.1}]
    )[0]
    assert high.weight > low.weight


def test_memory_item_dataclass_duck_typing():
    """Mapping 이 아닌 객체(예: MemoryItem) 도 attribute 로 읽힌다."""

    @dataclass
    class FakeType:
        value: str

    @dataclass
    class FakeItem:
        type: FakeType
        text: str
        confidence: float = 0.0
        source_ref: str = ""
        id: int = 0
        last_seen: datetime | None = None

    item = FakeItem(
        type=FakeType("active_project"),
        text="트레이딩 봇 프로젝트",
        confidence=0.8,
        last_seen=datetime(2026, 6, 30, 12, 0, 0),
    )
    signals = signals_from_memory_items([item])
    assert len(signals) == 1
    assert signals[0].source == "active_project"
    assert signals[0].last_seen == "2026-06-30T12:00:00"


# --------------------------------------------------------------------------- #
# insights — promoted/고신뢰만 채택
# --------------------------------------------------------------------------- #


def test_insights_filter_low_confidence_non_promoted():
    signals = signals_from_insights(
        [
            {"topic": "반도체", "text": "반도체 사이클 관심", "confidence": 0.9, "is_promoted": False},
            {"topic": "잡담", "text": "단발 관측", "confidence": 0.3, "is_promoted": False},
            {"topic": "프로모", "text": "승격된 insight", "confidence": 0.4, "is_promoted": True},
        ]
    )
    topics = {s.source_ref for s in signals}
    assert topics == {"반도체", "프로모"}  # 저신뢰 비승격은 제외


def test_promoted_insight_gets_base_weight():
    signals = signals_from_insights(
        [{"topic": "T", "text": "승격", "confidence": 0.65, "is_promoted": True}]
    )
    assert signals[0].source == "insight"
    assert signals[0].weight >= 0.5


# --------------------------------------------------------------------------- #
# auto_reports — 항상 낮은 가중치
# --------------------------------------------------------------------------- #


def test_auto_reports_always_below_threshold():
    signals = signals_from_auto_reports(
        ["뉴스 브리핑 1", "뉴스 브리핑 2", {"text": "뉴스 브리핑 3"}]
    )
    assert len(signals) == 3
    assert all(s.weight < AUTO_REPORT_MAX_WEIGHT for s in signals)
    assert all(s.weight < 0.5 for s in signals)
    assert all(s.source == "auto_report" for s in signals)


# --------------------------------------------------------------------------- #
# 통합 — 정렬/감사 필드
# --------------------------------------------------------------------------- #


def test_extract_topic_hints_sorted_by_weight_desc():
    signals = extract_topic_hints(
        user_messages=["삼성전자 실적 분석해줘"],
        memory_items=[{"type": "cluster_summary", "text": "잡담 클러스터"}],
        auto_reports=["일반 뉴스"],
    )
    weights = [s.weight for s in signals]
    assert weights == sorted(weights, reverse=True)


def test_every_signal_records_source_weight_confidence():
    signals = extract_topic_hints(
        user_messages=["엔비디아 분석해줘"],
        memory_items=[{"type": "decision", "text": "투자 비중 결정"}],
        insights=[{"topic": "AI", "text": "AI 관심", "is_promoted": True}],
        auto_reports=["뉴스"],
    )
    assert signals  # 비어 있지 않음
    for s in signals:
        assert isinstance(s, InterestSignal)
        assert s.source
        assert 0.0 <= s.weight <= 1.0
        assert 0.0 <= s.confidence <= 1.0
        assert s.topic_hint


def test_empty_inputs_return_empty_list():
    assert extract_topic_hints() == []


def test_signals_with_blank_hint_are_dropped():
    # topic_hint 가 비는 입력(공백만)은 결과에서 제외된다.
    signals = extract_topic_hints(user_messages=["   "], auto_reports=[""])
    assert signals == []
