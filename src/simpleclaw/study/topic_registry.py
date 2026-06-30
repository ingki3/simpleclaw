"""진화형 study topic registry — 생성·승격·감쇠·아카이브 생애주기 관리.

사용자의 관심사는 고정되지 않는다. 새 주제가 자동으로 감지되고(candidate), 반복적인
관심을 받으면 매일 공부 대상이 되며(active), 운영자가 핵심 관심사로 고정할 수 있고
(pinned), 한동안 신호가 끊기면 관심이 식고(cooling) 결국 검색 폴백 용도로만 남는다
(archived). 이 모듈은 그 생애주기를 한 곳에서 관리한다.

설계 결정:
- **점수 단일화**: topic 의 모든 상태 전이는 :mod:`scorer` 가 만든 단일 점수에 의존한다.
  user_interest / 반복 언급 / 신선도 필요 / 세상 중요도 / 최근성을 한 숫자로 합성하고,
  ``min_interest_score`` (active 승격), ``promote_threshold`` (상시 추적 승격) 임계값과
  비교한다.
- **시간 기반 감쇠**: 마지막 신호 이후 경과 일수가 ``decay_after_days`` 를 넘으면 cooling,
  그 두 배(또는 정책 배수)를 넘으면 archived 로 내려간다. "본 적 있음 ≠ 관심사" 원칙
  (docs/agent-study-wiki.md §2)을 코드로 강제하는 1차 방어선이다.
- **승격된 주제는 더 오래 추적**: ``peak_score`` 가 ``promote_threshold`` 를 넘은 적 있는
  주제는 감쇠/아카이브 창을 늘려, 반복 관심을 보인 핵심 주제가 단기간 신호 공백으로
  성급히 식지 않게 한다.
- **운영자 pin 은 sticky**: ``pinned`` 상태는 자동 전이 대상에서 제외한다. 운영자가
  명시적으로 unpin 하기 전까지 항상 daily study 대상으로 유지된다.
- **시간 주입 가능**: ``now_fn`` 으로 현재 시각을 주입받아 테스트가 감쇠/아카이브를
  결정적으로 재현할 수 있다.

source_planner 의 :class:`~simpleclaw.study.source_planner.StudyTopic` Protocol 을
:class:`Topic` 이 그대로 만족하므로, registry 의 active topic 을 곧장
``plan_fetch_requests`` 에 넘길 수 있다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable

from simpleclaw.study.scorer import (
    ScoreWeights,
    compute_topic_score,
    normalize_mentions,
    recency_decay_factor,
)
from simpleclaw.study.source_planner import TopicKind

NowFn = Callable[[], datetime]


def _utcnow() -> datetime:
    """기본 now 제공자. timezone-aware UTC 로 통일한다(감쇠 계산 일관성)."""
    return datetime.now(timezone.utc)


class TopicState(StrEnum):
    """topic 의 생애주기 상태.

    ``ARCHIVED`` 는 study_status 운영 도구의 archived 집합(archived/deleted/retired)과
    호환되도록 ``"archived"`` 문자열을 그대로 쓴다.
    """

    CANDIDATE = "candidate"  # 새로 감지됐지만 아직 공부 대상 아님
    ACTIVE = "active"  # daily study 대상
    PINNED = "pinned"  # 운영자가 고정한 핵심 관심사
    COOLING = "cooling"  # 관심도 감소 중
    ARCHIVED = "archived"  # 검색 fallback 에서만 사용


# daily study 가 매일 수집 대상으로 삼는 상태 집합.
_STUDY_STATES = frozenset({TopicState.ACTIVE, TopicState.PINNED})


class SignalSource(StrEnum):
    """관심 신호의 출처.

    ``USER`` / ``DREAMING`` 은 사용자와 결부된 관심으로 보아 topic kind 를
    ``USER_INTEREST`` 로 올리고, ``NEWS`` 는 일반 뉴스라 별도 관심 신호가 없으면
    ``GENERAL_NEWS`` 로 둔다.
    """

    USER = "user"
    DREAMING = "dreaming"
    NEWS = "news"


@dataclass(frozen=True)
class InterestSignal:
    """topic 에 관심을 부여하는 단일 신호.

    사용자 발화, Dreaming 파이프라인 결과, 일반 뉴스 등이 이 형태로 registry 에
    들어온다. 모든 점수 신호(0~1)는 선택적이며, 주어지지 않으면 0(또는 freshness 는
    중립 0.5)으로 둔다.

    Attributes:
        topic_id: topic 식별자(같은 주제는 같은 id 로 합쳐진다).
        label: 사람이 읽을 주제명(검색 query 로도 쓰인다).
        category: source policy 매핑에 쓰는 분류(예: ai-industry).
        source: 신호 출처.
        user_interest: 사용자 관심 강도(0~1). NEWS 는 보통 0.
        global_importance: 세상에서의 중요도(0~1).
        freshness_need: 시효가 짧아 자주 갱신해야 하는 정도(0~1).
        at: 신호 발생 시각. None 이면 record 시점의 now 를 쓴다.
    """

    topic_id: str
    label: str
    category: str = "general"
    source: SignalSource = SignalSource.USER
    user_interest: float = 0.0
    global_importance: float = 0.0
    freshness_need: float = 0.5
    at: datetime | None = None

    @property
    def implied_kind(self) -> TopicKind:
        """신호 출처로부터 topic kind 를 추론한다(NEWS 만 general_news)."""
        return (
            TopicKind.GENERAL_NEWS
            if self.source == SignalSource.NEWS
            else TopicKind.USER_INTEREST
        )


@dataclass
class Topic:
    """registry 가 추적하는 하나의 study 주제(가변 — 생애주기에 따라 변한다).

    :class:`~simpleclaw.study.source_planner.StudyTopic` Protocol 이 요구하는
    ``topic_id/label/category/kind/max_sources/freshness_hours`` 를 모두 가지므로,
    active topic 을 곧장 fetch 계획에 넘길 수 있다.
    """

    topic_id: str
    label: str
    category: str
    kind: TopicKind
    state: TopicState
    created_at: datetime
    updated_at: datetime
    last_signal_at: datetime
    # 점수 신호의 누적 상태(record 마다 갱신).
    user_interest: float = 0.0
    global_importance: float = 0.0
    freshness_need: float = 0.5
    mention_count: int = 0
    # 가장 신선했을 때의 최대 점수 — promote_threshold 통과 판정/감쇠 창 확장에 쓴다.
    peak_score: float = 0.0
    # 마지막으로 계산된 점수(serialization / 조회용).
    interest_score: float = 0.0
    last_studied_at: datetime | None = None
    # source policy 가 쓰는 수집 파라미터(StudyTopic Protocol).
    max_sources: int = 5
    freshness_hours: int = 24

    @property
    def is_study_target(self) -> bool:
        """daily study 수집 대상인지(active 또는 pinned)."""
        return self.state in _STUDY_STATES

    @property
    def is_archived(self) -> bool:
        """검색 폴백 전용(archived)인지."""
        return self.state == TopicState.ARCHIVED


@dataclass(frozen=True)
class TopicEvolutionPolicy:
    """topic 생애주기 전이 정책(config ``study.topic_evolution`` 매핑).

    Attributes:
        auto_create: 신호에서 candidate 주제를 자동 생성할지 여부.
        min_interest_score: candidate → active 승격 최소 점수.
        promote_threshold: 상시 추적(감쇠 창 확장)으로 보는 점수 임계값.
        decay_after_days: 신호 공백이 이 일수를 넘으면 cooling 으로 내린다.
        cooling_grace_multiplier: archived 까지의 추가 유예(decay_after_days 배수).
            archived 전이는 ``decay_after_days * (1 + grace)`` 일에 일어난다.
        promote_decay_multiplier: peak_score 가 promote_threshold 를 넘은 주제의
            감쇠/아카이브 창을 늘리는 배수(상시 추적).
        mention_saturation: 반복 언급 정규화 포화 상수(:func:`normalize_mentions`).
        weights: 점수 가중치.
    """

    auto_create: bool = True
    min_interest_score: float = 0.55
    promote_threshold: float = 0.70
    decay_after_days: float = 14.0
    cooling_grace_multiplier: float = 1.0
    promote_decay_multiplier: float = 2.0
    mention_saturation: float = 3.0
    weights: ScoreWeights = field(default_factory=ScoreWeights)

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> TopicEvolutionPolicy:
        """``study`` config dict 에서 정책을 만든다.

        ``study.topic_evolution`` 하위(auto_create/min_interest_score/
        promote_threshold/decay_after_days)를 읽고, 나머지는 기본값을 쓴다.
        ``topic_evolution`` 키가 없으면 config 자체를 evolution 매핑으로 본다.
        """
        evolution = config.get("topic_evolution", config)
        if not isinstance(evolution, Mapping):
            evolution = {}

        def _num(key: str, default: float) -> float:
            value = evolution.get(key, default)
            try:
                return float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default

        return cls(
            auto_create=bool(evolution.get("auto_create", True)),
            min_interest_score=_num("min_interest_score", 0.55),
            promote_threshold=_num("promote_threshold", 0.70),
            decay_after_days=_num("decay_after_days", 14.0),
        )

    @property
    def half_life_hours(self) -> float:
        """최근성 감쇠 반감기. decay 창과 연동해 decay_after_days 에서 0.5 가 되게 한다."""
        return max(1.0, self.decay_after_days * 24.0)


class TopicRegistry:
    """topic 들의 생성·승격·감쇠·아카이브를 관리하는 in-memory 레지스트리.

    영속화(topics.yaml 쓰기)는 후속 단계의 책임이고, 본 레지스트리는 record/evolve 로
    상태를 진화시키고 :meth:`to_records` 로 직렬화 가능한 표현만 제공한다.
    """

    def __init__(
        self,
        *,
        policy: TopicEvolutionPolicy | None = None,
        now_fn: NowFn = _utcnow,
    ) -> None:
        self._policy = policy or TopicEvolutionPolicy()
        self._now = now_fn
        self._topics: dict[str, Topic] = {}

    # -- 조회 ----------------------------------------------------------

    @property
    def policy(self) -> TopicEvolutionPolicy:
        return self._policy

    def get(self, topic_id: str) -> Topic | None:
        """id 로 topic 을 조회한다(없으면 None)."""
        return self._topics.get(topic_id)

    def all_topics(self) -> list[Topic]:
        """등록된 모든 topic 을 생성순으로 반환한다."""
        return list(self._topics.values())

    def study_targets(self) -> list[Topic]:
        """오늘 공부할 대상(active/pinned)만 반환한다.

        ``plan_fetch_requests`` 에 그대로 넘길 수 있다(StudyTopic Protocol 만족).
        """
        return [t for t in self._topics.values() if t.is_study_target]

    def archived_topics(self) -> list[Topic]:
        """검색 폴백 전용(archived) topic 만 반환한다."""
        return [t for t in self._topics.values() if t.is_archived]

    # -- 신호 수용 -----------------------------------------------------

    def record(self, signal: InterestSignal) -> Topic | None:
        """관심 신호 하나를 반영해 topic 을 생성/갱신하고 상태를 재계산한다.

        기존 topic 이면 누적 신호(언급 수↑, 관심/중요도/신선도 max, 마지막 신호 시각)를
        갱신하고, 없으면 ``auto_create`` 일 때만 candidate 로 새로 만든다. 갱신 후
        점수와 상태를 즉시 재계산한다.

        Returns:
            반영된 topic. ``auto_create=False`` 이고 신규 주제면 ``None``.
        """
        now = self._now()
        signal_at = signal.at or now
        topic = self._topics.get(signal.topic_id)

        if topic is None:
            if not self._policy.auto_create:
                return None
            topic = Topic(
                topic_id=signal.topic_id,
                label=signal.label,
                category=signal.category,
                kind=signal.implied_kind,
                state=TopicState.CANDIDATE,
                created_at=now,
                updated_at=now,
                last_signal_at=signal_at,
                user_interest=_clamp01(signal.user_interest),
                global_importance=_clamp01(signal.global_importance),
                freshness_need=_clamp01(signal.freshness_need),
                mention_count=1,
            )
            self._topics[topic.topic_id] = topic
        else:
            topic.mention_count += 1
            # 관심/중요도/신선도는 max 로 누적 — 한 번이라도 강한 신호면 그 강도를 유지.
            topic.user_interest = max(topic.user_interest, _clamp01(signal.user_interest))
            topic.global_importance = max(
                topic.global_importance, _clamp01(signal.global_importance)
            )
            topic.freshness_need = max(
                topic.freshness_need, _clamp01(signal.freshness_need)
            )
            # 메타데이터는 최신 비어있지 않은 값으로 갱신.
            if signal.label:
                topic.label = signal.label
            if signal.category:
                topic.category = signal.category
            # kind 는 user_interest 방향으로만 승급(뉴스 신호가 사용자 관심을 깎지 않음).
            if signal.implied_kind == TopicKind.USER_INTEREST:
                topic.kind = TopicKind.USER_INTEREST
            # 신호 시각은 가장 최신만 반영(과거 신호 재생 방어).
            topic.last_signal_at = max(topic.last_signal_at, signal_at)

        self._refresh(topic, now)
        return topic

    def ingest_dreaming_signals(
        self, signals: Iterable[InterestSignal]
    ) -> list[Topic]:
        """Dreaming 파이프라인이 surface 한 신호들을 일괄 반영한다.

        DoD: "Dreaming signal 기반 topic 생성이 가능하다." 각 신호의 ``source`` 는
        보통 :data:`SignalSource.DREAMING` 이며, 사용자와 결부된 관심으로 취급된다.

        Returns:
            생성/갱신된 topic 목록(auto_create=False 로 무시된 신규 신호는 제외).
        """
        touched: list[Topic] = []
        for signal in signals:
            topic = self.record(signal)
            if topic is not None:
                touched.append(topic)
        return touched

    # -- 운영자 조작 ---------------------------------------------------

    def pin(self, topic_id: str) -> Topic | None:
        """topic 을 운영자 핵심 관심사로 고정한다(자동 감쇠 면제)."""
        topic = self._topics.get(topic_id)
        if topic is None:
            return None
        topic.state = TopicState.PINNED
        topic.updated_at = self._now()
        return topic

    def unpin(self, topic_id: str) -> Topic | None:
        """pin 을 해제하고 점수/경과시간 기반 상태로 되돌린다."""
        topic = self._topics.get(topic_id)
        if topic is None:
            return None
        if topic.state == TopicState.PINNED:
            self._refresh(topic, self._now())
        return topic

    def mark_studied(self, topic_id: str, *, at: datetime | None = None) -> Topic | None:
        """daily study 가 이 topic 을 수집했음을 기록한다(last_studied_at 갱신)."""
        topic = self._topics.get(topic_id)
        if topic is None:
            return None
        topic.last_studied_at = at or self._now()
        topic.updated_at = self._now()
        return topic

    # -- 생애주기 진화 -------------------------------------------------

    def evolve(self) -> list[Topic]:
        """모든(비-pinned) topic 의 점수와 상태를 현재 시각 기준으로 재계산한다.

        daily run 시작 전 호출해 신호 공백이 길어진 일회성 주제를 cooling/archived 로
        내리고, 점수가 회복된 주제를 active 로 되돌린다.

        Returns:
            상태가 바뀐 topic 목록.
        """
        now = self._now()
        changed: list[Topic] = []
        for topic in self._topics.values():
            if topic.state == TopicState.PINNED:
                # pinned 는 점수만 갱신하고 상태는 건드리지 않는다(sticky).
                topic.interest_score = self._score(topic, now)
                continue
            before = topic.state
            self._refresh(topic, now)
            if topic.state != before:
                changed.append(topic)
        return changed

    # -- 직렬화 --------------------------------------------------------

    def to_records(self) -> list[dict[str, object]]:
        """topics.yaml 호환 dict 목록으로 직렬화한다.

        study_status 운영 도구(`StudyTopicView`)가 읽는 키
        (id/title/status/interest_score/created_at/updated_at/last_studied_at 등)와
        정렬한다.
        """
        return [self._to_record(t) for t in self._topics.values()]

    def _to_record(self, topic: Topic) -> dict[str, object]:
        return {
            "id": topic.topic_id,
            "title": topic.label,
            "category": topic.category,
            "kind": str(topic.kind),
            "status": str(topic.state),
            "interest_score": round(topic.interest_score, 4),
            "peak_score": round(topic.peak_score, 4),
            "mention_count": topic.mention_count,
            "created_at": _iso(topic.created_at),
            "updated_at": _iso(topic.updated_at),
            "last_signal_at": _iso(topic.last_signal_at),
            "last_studied_at": _iso(topic.last_studied_at),
        }

    # -- 내부: 점수/상태 계산 ------------------------------------------

    def _refresh(self, topic: Topic, now: datetime) -> None:
        """점수를 재계산하고, 그 점수와 경과시간으로 상태를 다시 정한다."""
        score = self._score(topic, now)
        topic.interest_score = score
        topic.peak_score = max(topic.peak_score, score)
        topic.state = self._next_state(topic, now)
        topic.updated_at = now

    def _score(self, topic: Topic, now: datetime) -> float:
        """topic 의 누적 신호 + 경과시간으로 0~1 점수를 만든다."""
        age_hours = max(0.0, (now - topic.last_signal_at).total_seconds() / 3600.0)
        return compute_topic_score(
            user_interest=topic.user_interest,
            repeated_mentions=normalize_mentions(
                topic.mention_count, saturation=self._policy.mention_saturation
            ),
            freshness_need=topic.freshness_need,
            global_importance=topic.global_importance,
            recency_decay=recency_decay_factor(
                age_hours, half_life_hours=self._policy.half_life_hours
            ),
            weights=self._policy.weights,
        )

    def _next_state(self, topic: Topic, now: datetime) -> TopicState:
        """점수/경과 일수로 다음 상태를 결정한다(pinned 는 호출 전 제외).

        경과 일수가 cooling/archive 창을 넘으면 시간이 점수를 이긴다(일회성 주제는
        결국 식는다). 그 전이면 점수가 ``min_interest_score`` 이상일 때만 active.
        ``peak_score`` 가 ``promote_threshold`` 를 넘은 적 있는 주제는 감쇠/아카이브
        창을 ``promote_decay_multiplier`` 배 늘려 더 오래 추적한다.
        """
        idle_days = max(0.0, (now - topic.last_signal_at).total_seconds() / 86400.0)

        decay_days = self._policy.decay_after_days
        if topic.peak_score >= self._policy.promote_threshold:
            decay_days *= self._policy.promote_decay_multiplier
        archive_days = decay_days * (1.0 + self._policy.cooling_grace_multiplier)

        if idle_days >= archive_days:
            return TopicState.ARCHIVED
        if idle_days >= decay_days:
            return TopicState.COOLING
        if topic.interest_score >= self._policy.min_interest_score:
            return TopicState.ACTIVE
        return TopicState.CANDIDATE


def _clamp01(value: float) -> float:
    """입력 신호를 0~1 로 자른다(scorer 와 동일 규칙, 신규 topic 초기화용)."""
    return max(0.0, min(1.0, value))


def _iso(value: datetime | None) -> str | None:
    """datetime 을 ISO 8601 문자열로(없으면 None)."""
    return value.isoformat() if value is not None else None
