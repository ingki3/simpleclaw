"""Study Wiki 주제 레지스트리 단위 테스트.

두 레지스트리를 함께 검증한다.
- 영속 레지스트리(:class:`TopicRegistry`): ``topics.yaml`` 왕복, 위키 루트 초기화
  (멱등성), 손편집 관대성.
- 진화형 레지스트리(:class:`EvolvingTopicRegistry`): 생성·승격·감쇠·아카이브 생애주기,
  Dreaming signal 기반 topic 생성, 일회성 topic 의 cooling/archive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from simpleclaw.study.paths import (
    daily_dir,
    init_wiki_root,
    topic_page_path,
    topics_dir,
    topics_yaml_path,
)
from simpleclaw.study.source_planner import TopicKind, plan_fetch_requests
from simpleclaw.study.topic_registry import (
    EvolvingTopicRegistry,
    SignalSource,
    TopicEvolutionPolicy,
    TopicRegistry,
    TopicSignal,
    TopicState,
    load_topics,
    save_topics,
)
from simpleclaw.study.types import StudyTopic

# ======================================================================
# 영속 레지스트리: topics.yaml ↔ StudyTopic
# ======================================================================


def test_init_wiki_root_creates_structure(tmp_path: Path):
    root = init_wiki_root(tmp_path / "study")
    assert root.is_dir()
    assert topics_dir(tmp_path / "study").is_dir()
    assert daily_dir(tmp_path / "study").is_dir()
    topics_file = topics_yaml_path(tmp_path / "study")
    assert topics_file.is_file()
    # 시드된 빈 레지스트리는 빈 목록으로 로드되어야 한다.
    assert load_topics(topics_file) == []


def test_init_wiki_root_is_idempotent_and_preserves_topics(tmp_path: Path):
    base = tmp_path / "study"
    init_wiki_root(base)
    save_topics([StudyTopic(id="t1", label="T1")], topics_yaml_path(base))
    # 다시 초기화해도 기존 topics.yaml 을 덮어쓰지 않는다.
    init_wiki_root(base)
    loaded = load_topics(topics_yaml_path(base))
    assert [t.id for t in loaded] == ["t1"]


def test_topic_page_path_rejects_path_injection(tmp_path: Path):
    base = tmp_path / "study"
    assert topic_page_path("ai-openai", base).name == "ai-openai.md"
    for bad in ["../escape", "a/b", "", ".."]:
        try:
            topic_page_path(bad, base)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_save_and_load_topics_round_trip(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    topics = [
        StudyTopic(
            id="ai-industry-openai",
            label="OpenAI",
            description="생성형 AI 대표 기업",
            priority="high",
            tags=["ai", "industry"],
            interest_score=0.8,
            importance_score=0.6,
            metadata={"watch": True},
        ),
        StudyTopic(id="kr-economy", label="한국 경제"),
    ]
    save_topics(topics, path)
    loaded = load_topics(path)

    assert [t.id for t in loaded] == ["ai-industry-openai", "kr-economy"]
    first = loaded[0]
    assert first.priority == "high"
    assert first.tags == ["ai", "industry"]
    assert first.interest_score == 0.8
    assert first.metadata == {"watch": True}


def test_load_topics_missing_file_returns_empty(tmp_path: Path):
    assert load_topics(tmp_path / "nope.yaml") == []


def test_load_topics_tolerates_unknown_keys_and_missing_id(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n"
        "  - id: valid\n"
        "    label: Valid\n"
        "    unexpected_key: 123\n"
        "  - label: 'id 없는 항목'\n",  # id 누락 → 건너뜀
        encoding="utf-8",
    )
    loaded = load_topics(path)
    assert [t.id for t in loaded] == ["valid"]
    assert loaded[0].label == "Valid"


def test_topic_registry_crud(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    reg = TopicRegistry(path=path)
    assert len(reg) == 0

    reg.upsert(StudyTopic(id="t1", label="T1"))
    reg.upsert(StudyTopic(id="t2", label="T2"))
    assert "t1" in reg
    assert len(reg) == 2

    # 같은 id 로 upsert 하면 교체.
    reg.upsert(StudyTopic(id="t1", label="T1-updated"))
    assert reg.get("t1").label == "T1-updated"
    assert len(reg) == 2

    reg.save()
    reloaded = TopicRegistry.load(path)
    assert [t.id for t in reloaded.list()] == ["t1", "t2"]

    assert reg.remove("t1") is True
    assert reg.remove("missing") is False
    assert "t1" not in reg


# ======================================================================
# 진화형 레지스트리: 생성·승격·감쇠·아카이브 생애주기
# ======================================================================

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    """테스트용 주입 가능한 단조 시계."""

    def __init__(self, start: datetime = _T0) -> None:
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, *, days: float = 0.0, hours: float = 0.0) -> None:
        self.t += timedelta(days=days, hours=hours)


def _registry(clock: Clock, **policy_kwargs) -> EvolvingTopicRegistry:
    return EvolvingTopicRegistry(
        policy=TopicEvolutionPolicy(**policy_kwargs), now_fn=clock
    )


def _user_signal(topic_id: str, *, interest: float, fresh: float = 0.5, **kw) -> TopicSignal:
    return TopicSignal(
        topic_id=topic_id,
        label=kw.pop("label", topic_id),
        category=kw.pop("category", "ai-industry"),
        source=SignalSource.USER,
        user_interest=interest,
        freshness_need=fresh,
        **kw,
    )


class TestCreationAndStates:
    def test_weak_signal_creates_candidate(self):
        clock = Clock()
        reg = _registry(clock)
        topic = reg.record(_user_signal("t", interest=0.3))
        assert topic is not None
        assert topic.state == TopicState.CANDIDATE
        assert topic.mention_count == 1

    def test_strong_signal_becomes_active(self):
        clock = Clock()
        reg = _registry(clock)
        topic = reg.record(_user_signal("t", interest=0.9, fresh=0.6))
        assert topic.state == TopicState.ACTIVE
        assert topic.interest_score >= 0.55

    def test_repeated_mentions_promote_candidate_to_active(self):
        clock = Clock()
        reg = _registry(clock)
        # 첫 신호는 약해 candidate.
        reg.record(_user_signal("t", interest=0.4, fresh=0.4))
        assert reg.get("t").state == TopicState.CANDIDATE
        # 반복 + 강한 관심으로 active 승격.
        for _ in range(4):
            reg.record(_user_signal("t", interest=0.8, fresh=0.6))
        assert reg.get("t").state == TopicState.ACTIVE

    def test_auto_create_disabled_ignores_new_topics(self):
        clock = Clock()
        reg = _registry(clock, auto_create=False)
        assert reg.record(_user_signal("t", interest=0.9)) is None
        assert reg.all_topics() == []


class TestDreamingIngestion:
    def test_ingest_dreaming_signals_creates_user_interest_topics(self):
        clock = Clock()
        reg = _registry(clock)
        signals = [
            TopicSignal(
                topic_id="rust",
                label="Rust async",
                category="dev",
                source=SignalSource.DREAMING,
                user_interest=0.8,
                freshness_need=0.6,
            ),
            TopicSignal(
                topic_id="kr-housing",
                label="전세 시장",
                category="markets",
                source=SignalSource.DREAMING,
                user_interest=0.3,
            ),
        ]
        touched = reg.ingest_dreaming_signals(signals)
        assert {t.topic_id for t in touched} == {"rust", "kr-housing"}
        # dreaming 신호는 사용자 관심으로 취급 → kind 는 USER_INTEREST.
        assert reg.get("rust").kind == TopicKind.USER_INTEREST


class TestNewsKind:
    def test_news_signal_is_general_news(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(
            TopicSignal(
                topic_id="quake",
                label="지진 속보",
                category="general",
                source=SignalSource.NEWS,
                global_importance=0.9,
            )
        )
        assert reg.get("quake").kind == TopicKind.GENERAL_NEWS

    def test_user_signal_upgrades_news_topic_kind(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(
            TopicSignal(
                topic_id="ai",
                label="AI 규제",
                source=SignalSource.NEWS,
                global_importance=0.7,
            )
        )
        assert reg.get("ai").kind == TopicKind.GENERAL_NEWS
        reg.record(_user_signal("ai", interest=0.8, label="AI 규제"))
        # 사용자가 관심을 보이면 일반 뉴스에서 사용자 관심으로 승급(다운그레이드 없음).
        assert reg.get("ai").kind == TopicKind.USER_INTEREST


class TestDecayAndArchive:
    def _make_active_non_promoted(self, reg: EvolvingTopicRegistry) -> None:
        # peak_score 가 promote_threshold(0.70) 미만이면서 active 인 topic.
        topic = reg.record(_user_signal("t", interest=0.7, fresh=0.8))
        assert topic.state == TopicState.ACTIVE
        assert topic.peak_score < 0.70

    def test_one_shot_topic_cools_then_archives(self):
        clock = Clock()
        reg = _registry(clock)
        self._make_active_non_promoted(reg)

        # 15일 신호 공백(>14) → cooling.
        clock.advance(days=15)
        changed = reg.evolve()
        assert reg.get("t").state == TopicState.COOLING
        assert reg.get("t") in changed

        # 누적 29일(>28=14*2) → archived.
        clock.advance(days=14)
        reg.evolve()
        assert reg.get("t").state == TopicState.ARCHIVED
        assert reg.get("t").is_archived

    def test_promoted_topic_resists_short_gap(self):
        clock = Clock()
        reg = _registry(clock)
        # 반복·고관심으로 peak_score >= 0.70 → 승격.
        for _ in range(4):
            reg.record(
                _user_signal("hot", interest=0.9, fresh=0.7, global_importance=0.3)
            )
        assert reg.get("hot").peak_score >= 0.70

        # 15일 공백: 승격 주제는 감쇠 창이 2배(28일)라 아직 cooling 아님.
        clock.advance(days=15)
        reg.evolve()
        assert reg.get("hot").state == TopicState.ACTIVE

    def test_archived_topic_revives_on_new_signal(self):
        clock = Clock()
        reg = _registry(clock)
        self._make_active_non_promoted(reg)
        clock.advance(days=40)
        reg.evolve()
        assert reg.get("t").state == TopicState.ARCHIVED

        # 새 강한 신호가 오면 다시 active 로 살아난다.
        reg.record(_user_signal("t", interest=0.9, fresh=0.7))
        assert reg.get("t").state == TopicState.ACTIVE


class TestPinning:
    def test_pinned_topic_is_immune_to_decay(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(_user_signal("t", interest=0.7, fresh=0.8))
        reg.pin("t")
        assert reg.get("t").state == TopicState.PINNED

        clock.advance(days=60)
        reg.evolve()
        # pin 은 sticky — 60일 공백에도 archived 되지 않는다.
        assert reg.get("t").state == TopicState.PINNED

    def test_unpin_restores_recomputed_state(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(_user_signal("t", interest=0.7, fresh=0.8))
        reg.pin("t")
        clock.advance(days=40)
        reg.unpin("t")
        # unpin 후엔 경과시간 기준으로 archived.
        assert reg.get("t").state == TopicState.ARCHIVED

    def test_pinned_topic_remains_pinned_when_new_signal_recorded(self):
        # BIZ-408 회귀: pinned topic 은 record() 경로의 새 신호로 자동 demote 되면 안 된다.
        clock = Clock()
        reg = _registry(clock)
        # active 로 시작 → pin.
        reg.record(_user_signal("t", interest=0.9, fresh=0.6))
        assert reg.get("t").state == TopicState.ACTIVE
        reg.pin("t")
        assert reg.get("t").state == TopicState.PINNED

        # 약한 user 신호: 자동 전이라면 candidate 로 demote 될 점수지만 PINNED 유지.
        reg.record(_user_signal("t", interest=0.1, fresh=0.1))
        assert reg.get("t").state == TopicState.PINNED

        # Dreaming 신호도 마찬가지로 상태를 건드리지 않는다.
        reg.record(
            TopicSignal(
                topic_id="t",
                label="t",
                source=SignalSource.DREAMING,
                user_interest=0.2,
            )
        )
        assert reg.get("t").state == TopicState.PINNED

        # 일반 뉴스 신호도 PINNED 를 유지한다.
        reg.record(
            TopicSignal(
                topic_id="t",
                label="t",
                source=SignalSource.NEWS,
                global_importance=0.3,
            )
        )
        assert reg.get("t").state == TopicState.PINNED

    def test_pinned_topic_updates_score_without_demoting(self):
        # BIZ-408 회귀: pinned 라도 누적 신호/점수/last_signal_at 갱신은 계속돼야 한다.
        clock = Clock()
        reg = _registry(clock)
        reg.record(_user_signal("t", interest=0.4, fresh=0.4))
        reg.pin("t")
        before_score = reg.get("t").interest_score
        before_mentions = reg.get("t").mention_count

        clock.advance(hours=1)
        reg.record(_user_signal("t", interest=0.95, fresh=0.9, global_importance=0.5))
        topic = reg.get("t")
        # 상태는 PINNED 로 고정.
        assert topic.state == TopicState.PINNED
        # 점수/언급/마지막 신호 시각은 갱신.
        assert topic.mention_count == before_mentions + 1
        assert topic.interest_score > before_score
        assert topic.last_signal_at == clock.t

        # unpin 하면 갱신된 강한 점수 기준으로 재계산되어 active 로 복귀.
        reg.unpin("t")
        assert reg.get("t").state == TopicState.ACTIVE


class TestStudyTargetsAndSerialization:
    def test_study_targets_include_active_and_pinned_only(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(_user_signal("active1", interest=0.9, fresh=0.6))
        reg.record(_user_signal("cand", interest=0.2))
        reg.record(_user_signal("pinned1", interest=0.4))
        reg.pin("pinned1")

        ids = {t.topic_id for t in reg.study_targets()}
        assert ids == {"active1", "pinned1"}

    def test_study_targets_feed_source_planner(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(_user_signal("ai", interest=0.9, fresh=0.6, category="ai-industry"))
        requests = plan_fetch_requests(reg.study_targets())
        assert requests  # Topic 이 StudyTopic Protocol 을 만족
        assert all(r.topic_id == "ai" for r in requests)

    def test_to_records_shape(self):
        clock = Clock()
        reg = _registry(clock)
        reg.record(_user_signal("ai", interest=0.9, fresh=0.6, label="AI 산업"))
        reg.mark_studied("ai")
        (record,) = reg.to_records()
        assert record["id"] == "ai"
        assert record["title"] == "AI 산업"
        assert record["status"] == "active"
        assert 0.0 <= record["interest_score"] <= 1.0
        assert record["last_studied_at"] is not None
        assert record["created_at"] is not None


class TestPolicyFromConfig:
    def test_from_config_reads_topic_evolution(self):
        policy = TopicEvolutionPolicy.from_config(
            {
                "topic_evolution": {
                    "auto_create": False,
                    "min_interest_score": 0.6,
                    "promote_threshold": 0.85,
                    "decay_after_days": 7,
                }
            }
        )
        assert policy.auto_create is False
        assert policy.min_interest_score == pytest.approx(0.6)
        assert policy.promote_threshold == pytest.approx(0.85)
        assert policy.decay_after_days == pytest.approx(7.0)

    def test_from_config_defaults_on_missing_section(self):
        policy = TopicEvolutionPolicy.from_config({})
        assert policy.auto_create is True
        assert policy.min_interest_score == pytest.approx(0.55)
        assert policy.decay_after_days == pytest.approx(14.0)


# ======================================================================
# BIZ-434: evolution 필드 round-trip
# ======================================================================


def test_topic_registry_round_trips_evolution_fields(tmp_path: Path):
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n"
        "  - id: llm-routing\n"
        "    label: LLM routing\n"
        "    status: active\n"
        "    category: ai-industry\n"
        "    kind: user_interest\n"
        "    search_queries:\n"
        "      - LLM routing agents latest\n"
        "    source_signals:\n"
        "      - source: user_message\n"
        "        source_ref: msg-1\n"
        "        weight: 0.72\n"
        "    mention_count: 3\n"
        "    peak_score: 0.81\n"
        "    last_signal_at: '2026-07-12T00:00:00+09:00'\n"
        "    last_studied_at: '2026-07-12T06:00:00+09:00'\n",
        encoding="utf-8",
    )

    registry = TopicRegistry.load(path)
    topic = registry.get("llm-routing")

    assert topic is not None
    assert topic.category == "ai-industry"
    assert topic.kind == "user_interest"
    assert topic.search_queries == ["LLM routing agents latest"]
    assert topic.source_signals[0]["source"] == "user_message"
    assert topic.mention_count == 3
    assert topic.peak_score == 0.81
    assert topic.last_signal_at == "2026-07-12T00:00:00+09:00"
    assert topic.last_studied_at == "2026-07-12T06:00:00+09:00"

    registry.save()
    reloaded = TopicRegistry.load(path).get("llm-routing")
    assert reloaded is not None
    assert reloaded.search_queries == ["LLM routing agents latest"]
    assert reloaded.source_signals[0]["source_ref"] == "msg-1"


def test_topic_from_dict_normalizes_malformed_containers(tmp_path: Path):
    """손편집으로 list/dict 자리에 스칼라가 들어와도 안전하게 정규화된다."""
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n"
        "  - id: broken\n"
        "    label: Broken\n"
        "    tags: not-a-list\n"
        "    search_queries: 12\n"
        "    source_signals: oops\n"
        "    metadata: nope\n",
        encoding="utf-8",
    )

    topic = TopicRegistry.load(path).get("broken")

    assert topic is not None
    assert topic.tags == []
    assert topic.search_queries == []
    assert topic.source_signals == []
    assert topic.metadata == {}
