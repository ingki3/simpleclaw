"""Study signal provider 검증 — merge/정렬/가중치 정책 (BIZ-434).

DoD:
- provider merge 가 가중치 내림차순으로 평탄화한다.
- user message 신호가 auto report 신호보다 항상 높은 가중치를 받는다.
- 콜백 기반 provider 는 원천 실패를 격리한다.
"""

from __future__ import annotations

from simpleclaw.study.interest_signals import (
    AUTO_REPORT_MAX_WEIGHT,
    InterestSignal,
)
from simpleclaw.study.signal_provider import (
    SourceBackedSignalProvider,
    StaticStudySignalProvider,
    build_interest_signals_from_sources,
    merge_signal_providers,
)


def test_static_signal_provider_returns_configured_signals():
    signal = InterestSignal(
        "AI agents", "AI agents", "user_message", weight=0.8, confidence=0.7
    )
    provider = StaticStudySignalProvider([signal])

    assert provider.collect() == [signal]


def test_merge_signal_providers_flattens_and_sorts():
    low = InterestSignal("low", "low", "auto_report", weight=0.2, confidence=0.1)
    high = InterestSignal("high", "high", "user_message", weight=0.9, confidence=0.7)

    signals = merge_signal_providers(
        [
            StaticStudySignalProvider([low]),
            StaticStudySignalProvider([high]),
        ]
    )

    assert [s.topic_hint for s in signals] == ["high", "low"]


def test_merge_signal_providers_drops_empty_hints():
    blank = InterestSignal("", "no hint", "user_message", weight=0.9)
    kept = InterestSignal("topic", "topic", "user_message", weight=0.5)

    signals = merge_signal_providers([StaticStudySignalProvider([blank, kept])])

    assert [s.topic_hint for s in signals] == ["topic"]


def test_build_interest_signals_prioritizes_user_messages_over_auto_reports():
    signals = build_interest_signals_from_sources(
        user_messages=[{"text": "LangGraph 없이 tool loop 유지하는 이유 다시 정리해줘"}],
        memory_items=[],
        insights=[],
        auto_reports=[{"text": "자동 주식 리포트"}],
    )

    assert signals[0].source == "user_message"
    assert signals[0].weight > signals[-1].weight
    assert signals[-1].source == "auto_report"
    assert signals[-1].weight < AUTO_REPORT_MAX_WEIGHT


def test_source_backed_provider_pulls_from_callbacks():
    provider = SourceBackedSignalProvider(
        fetch_user_messages=lambda: ["AI coding agents 비교해줘"],
        fetch_auto_reports=lambda: ["자동 뉴스 브리핑"],
    )

    signals = provider.collect()

    sources = {s.source for s in signals}
    assert "user_message" in sources
    assert "auto_report" in sources
    # user 신호가 auto 신호보다 앞(가중치 내림차순).
    assert signals[0].source == "user_message"


def test_source_backed_provider_isolates_failing_callback():
    def _boom():
        raise RuntimeError("store unavailable")

    provider = SourceBackedSignalProvider(
        fetch_user_messages=_boom,
        fetch_auto_reports=lambda: ["자동 뉴스 브리핑"],
    )

    signals = provider.collect()

    assert all(s.source == "auto_report" for s in signals)
    assert signals  # 실패한 원천만 빠지고 나머지는 수집된다.
