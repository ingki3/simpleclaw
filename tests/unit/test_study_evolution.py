"""Study topic evolution 검증 — 관심 신호 → topic 생성/승격/감쇠 (BIZ-434).

DoD:
- InterestSignal → TopicSignal 변환이 결정적 stable id 를 만든다.
- 강한 user/Dreaming 신호는 candidate 를 만들고 threshold 이상이면 active 승격.
- auto_report-only 신호는 active 로 승격되지 않는다.
- stale non-pinned topic 은 cooling/archive 로 감쇠된다.
- 영속 StudyTopic ↔ EvolvingTopicRegistry 매핑이 pinned seed 와 운영자 편집
  필드를 보존한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from simpleclaw.study.evolution import (
    EvolutionSummary,
    apply_interest_signals,
    interest_signal_to_topic_signal,
    merge_registry_into_raw_topics,
    registry_from_study_topics,
    registry_to_study_topics,
    slugify_topic_id,
)
from simpleclaw.study.interest_signals import InterestSignal
from simpleclaw.study.topic_registry import (
    EvolvingTopicRegistry,
    TopicEvolutionPolicy,
    TopicState,
)
from simpleclaw.study.types import StudyTopic

NOW = datetime(2026, 7, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# slugify / 변환
# ---------------------------------------------------------------------------


def test_slugify_topic_id_handles_korean_and_ascii():
    assert slugify_topic_id("AI coding agents / Codex") == "ai-coding-agents-codex"
    assert slugify_topic_id("한국 증시 코스피") == "korean-market-kospi"


def test_slugify_topic_id_is_stable_for_korean_only_text():
    first = slugify_topic_id("양자내성암호 전환 로드맵")
    second = slugify_topic_id("양자내성암호 전환 로드맵")
    assert first == second
    assert first.startswith("topic-")


def test_interest_signal_to_topic_signal_maps_weight_to_user_interest():
    signal = InterestSignal(
        topic_hint="AI coding agents",
        text="Codex CLI랑 Claude Code 비교해줘",
        source="user_message",
        source_ref="msg-1",
        weight=0.72,
        confidence=0.5,
    )

    topic_signal = interest_signal_to_topic_signal(signal, now=NOW)

    assert topic_signal.topic_id == "ai-coding-agents"
    assert topic_signal.label == "AI coding agents"
    assert topic_signal.category == "ai-industry"
    assert topic_signal.user_interest == 0.72
    assert topic_signal.global_importance == 0.5
    assert topic_signal.source.value == "user"
    assert topic_signal.at == NOW


def test_market_hint_maps_to_markets_category():
    signal = InterestSignal(
        topic_hint="KOSPI outlook",
        text="코스피 전망",
        source="user_message",
        weight=0.7,
        confidence=0.5,
    )
    topic_signal = interest_signal_to_topic_signal(signal, now=NOW)
    assert topic_signal.category == "markets"
    assert topic_signal.freshness_need == 0.7


def test_auto_report_signal_maps_to_news_source():
    signal = InterestSignal(
        topic_hint="Random automated news",
        text="자동 브리핑",
        source="auto_report",
        weight=0.25,
        confidence=0.1,
    )
    topic_signal = interest_signal_to_topic_signal(signal, now=NOW)
    assert topic_signal.source.value == "news"


# ---------------------------------------------------------------------------
# apply_interest_signals — 생성/승격/감쇠
# ---------------------------------------------------------------------------


def test_apply_interest_signals_creates_and_promotes_active_topic():
    registry = EvolvingTopicRegistry(
        policy=TopicEvolutionPolicy(min_interest_score=0.55),
        now_fn=lambda: NOW,
    )
    signals = [
        InterestSignal(
            topic_hint="AI coding agents",
            text="AI coding agents 조사해줘",
            source="user_message",
            weight=0.9,
            confidence=0.8,
            source_ref="msg-1",
        )
    ]

    summary = apply_interest_signals(registry, signals, now=NOW)
    topic = registry.get("ai-coding-agents")

    assert isinstance(summary, EvolutionSummary)
    assert topic is not None
    assert topic.state == TopicState.ACTIVE
    assert summary.created == 1
    assert summary.promoted >= 1
    assert "ai-coding-agents" in summary.touched_ids


def test_auto_report_signal_alone_does_not_promote_active_topic():
    registry = EvolvingTopicRegistry(now_fn=lambda: NOW)
    signals = [
        InterestSignal(
            topic_hint="Random automated news",
            text="자동 브리핑에서 본 뉴스",
            source="auto_report",
            weight=0.25,
            confidence=0.1,
        )
    ]

    apply_interest_signals(registry, signals, now=NOW)
    topic = registry.get("random-automated-news")

    assert topic is not None
    assert topic.state == TopicState.CANDIDATE


def test_repeated_signals_accumulate_into_same_topic():
    registry = EvolvingTopicRegistry(now_fn=lambda: NOW)
    signals = [
        InterestSignal("LLM routing", "LLM routing 알려줘", "user_message", weight=0.7),
        InterestSignal("LLM routing", "LLM routing 다시", "user_message", weight=0.75),
    ]

    summary = apply_interest_signals(registry, signals, now=NOW)
    topic = registry.get("llm-routing")

    assert topic is not None
    assert topic.mention_count == 2
    assert summary.created == 1
    assert summary.updated == 0  # 같은 run 내 재언급은 created 로만 집계


def test_stale_non_pinned_topic_decays_to_cooling_and_archive():
    policy = TopicEvolutionPolicy(decay_after_days=14.0)
    old_signal_at = (NOW - timedelta(days=20)).isoformat()

    def _stale_topic() -> StudyTopic:
        return StudyTopic(
            id="one-off-topic",
            label="One-off topic",
            status="active",
            interest_score=0.9,
            last_signal_at=old_signal_at,
        )

    registry = registry_from_study_topics(
        [_stale_topic()], policy=policy, now_fn=lambda: NOW
    )
    summary = apply_interest_signals(registry, [], now=NOW)

    topic = registry.get("one-off-topic")
    assert topic is not None
    assert topic.state == TopicState.COOLING
    assert "one-off-topic" in summary.cooled_ids

    # archive 창(2배 유예)을 넘기면 archived 로 내려간다.
    later = NOW + timedelta(days=40)
    registry_later = registry_from_study_topics(
        [_stale_topic()], policy=policy, now_fn=lambda: later
    )
    summary_later = apply_interest_signals(registry_later, [], now=later)
    assert registry_later.get("one-off-topic").state == TopicState.ARCHIVED
    assert "one-off-topic" in summary_later.archived_ids


def test_pinned_topic_is_sticky_during_evolution():
    registry = registry_from_study_topics(
        [
            StudyTopic(
                id="market-reports-us-kr",
                label="US/Korean market reports and watchpoints",
                status="pinned",
                category="markets",
                last_signal_at=(NOW - timedelta(days=90)).isoformat(),
            )
        ],
        now_fn=lambda: NOW,
    )

    apply_interest_signals(registry, [], now=NOW)

    assert registry.get("market-reports-us-kr").state == TopicState.PINNED


# ---------------------------------------------------------------------------
# 영속 StudyTopic ↔ registry 매핑
# ---------------------------------------------------------------------------


def test_registry_from_study_topics_preserves_pinned_seed():
    topics = [
        StudyTopic(
            id="market-reports-us-kr",
            label="US/Korean market reports and watchpoints",
            status="pinned",
            category="markets",
            interest_score=0.9,
            mention_count=5,
            last_signal_at="2026-07-01T00:00:00+00:00",
        )
    ]

    registry = registry_from_study_topics(topics, now_fn=lambda: NOW)
    topic = registry.get("market-reports-us-kr")

    assert topic is not None
    assert topic.state.value == "pinned"
    assert topic.category == "markets"
    assert topic.mention_count == 5
    assert topic.last_signal_at == datetime(2026, 7, 1, tzinfo=UTC)


def test_registry_from_study_topics_skips_unmapped_status():
    topics = [
        StudyTopic(id="paused-topic", label="Paused", status="paused"),
        StudyTopic(id="live-topic", label="Live", status="active"),
    ]

    registry = registry_from_study_topics(topics, now_fn=lambda: NOW)

    assert registry.get("paused-topic") is None
    assert registry.get("live-topic") is not None


def test_registry_to_study_topics_preserves_search_queries():
    original = StudyTopic(
        id="market-reports-us-kr",
        label="US/Korean market reports and watchpoints",
        status="pinned",
        category="markets",
        search_queries=["US stock market latest news"],
    )
    registry = registry_from_study_topics([original], now_fn=lambda: NOW)

    saved = registry_to_study_topics(registry, existing=[original])

    assert saved[0].id == "market-reports-us-kr"
    assert saved[0].status == "pinned"
    assert saved[0].search_queries == ["US stock market latest news"]


# ---------------------------------------------------------------------------
# raw dict 병합 — 운영 키 보존
# ---------------------------------------------------------------------------


def test_merge_preserves_unknown_operational_keys():
    raw = [
        {
            "id": "market-reports-us-kr",
            "title": "US/KR 시장 리포트",
            "label": "US/Korean market reports and watchpoints",
            "status": "pinned",
            "category": "markets",
            "source": "operator-bootstrap",
            "source_count": 42,
            "refresh_requested_at": "2026-07-11T00:00:00+00:00",
            "last_signal_at": "2026-07-01T00:00:00+00:00",
        }
    ]
    topics = [
        StudyTopic(
            id="market-reports-us-kr",
            label="US/Korean market reports and watchpoints",
            status="pinned",
            category="markets",
            last_signal_at="2026-07-01T00:00:00+00:00",
        )
    ]
    registry = registry_from_study_topics(topics, now_fn=lambda: NOW)
    apply_interest_signals(registry, [], now=NOW)

    merged = merge_registry_into_raw_topics(raw, registry, now=NOW)

    item = merged[0]
    assert item["source_count"] == 42  # 운영 키 보존
    assert item["refresh_requested_at"] == "2026-07-11T00:00:00+00:00"
    assert item["title"] == "US/KR 시장 리포트"
    assert item["status"] == "pinned"
    assert item["source"] == "operator-bootstrap"


def test_merge_appends_new_topic_with_canonical_schema_and_title():
    signal = InterestSignal(
        topic_hint="AI coding agents",
        text="AI coding agents 조사해줘",
        source="user_message",
        weight=0.9,
        confidence=0.8,
        source_ref="msg-1",
    )
    registry = EvolvingTopicRegistry(now_fn=lambda: NOW)
    apply_interest_signals(registry, [signal], now=NOW)

    merged = merge_registry_into_raw_topics([], registry, signals=[signal], now=NOW)

    assert len(merged) == 1
    item = merged[0]
    assert item["id"] == "ai-coding-agents"
    assert item["title"] == "AI coding agents"
    assert item["status"] == "active"
    assert item["source"] == "interest"
    assert item["source_signals"][0]["source"] == "user_message"
    assert item["source_signals"][0]["source_ref"] == "msg-1"
    assert item["last_signal_at"] == NOW.isoformat()


def test_merge_does_not_reset_last_signal_at_for_untouched_topics():
    """신호 없는 topic 에 now 폴백이 기록되면 감쇠가 영영 안 일어난다 — 금지."""
    raw = [{"id": "quiet", "label": "Quiet", "status": "active"}]
    topics = [StudyTopic(id="quiet", label="Quiet", status="active")]
    registry = registry_from_study_topics(topics, now_fn=lambda: NOW)
    apply_interest_signals(registry, [], now=NOW)

    merged = merge_registry_into_raw_topics(raw, registry, now=NOW)

    assert "last_signal_at" not in merged[0]


def test_merge_leaves_unmapped_status_rows_untouched():
    raw = [{"id": "paused-topic", "label": "Paused", "status": "paused", "custom": 1}]
    registry = registry_from_study_topics(
        [StudyTopic(id="paused-topic", label="Paused", status="paused")],
        now_fn=lambda: NOW,
    )

    merged = merge_registry_into_raw_topics(raw, registry, now=NOW)

    assert merged[0] == raw[0]
