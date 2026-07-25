"""Study topic evolution 오케스트레이션 — 관심 신호를 topic 생애주기에 반영한다.

관심 신호(:class:`~simpleclaw.study.interest_signals.InterestSignal` — 최근 대화/
Dreaming/승인된 memory insight)를 :class:`~simpleclaw.study.topic_registry.
EvolvingTopicRegistry` 의 :class:`TopicSignal` 로 변환·적용하고, 그 결과를 영속
``topics.yaml`` 스키마(:class:`~simpleclaw.study.types.StudyTopic` / raw dict)와
왕복 매핑한다.

설계 결정:
- **순수 변환 계층.** 이 모듈은 외부 저장소를 직접 읽지 않는다. caller(runner/
  signal provider)가 전달한 InterestSignal 목록만 변환/적용하고, 파일 I/O 는
  runner 계층의 책임이다.
- **stable topic id.** 같은 관심사가 매번 다른 id 로 흩어지면 반복 언급 누적이
  깨지므로, :func:`slugify_topic_id` 는 결정적이어야 한다. 한국어는 소수의 알려진
  구문 매핑 + 해시 폴백만 쓴다(과도한 음역 변환은 만들지 않는다 — 계획 Risk 2).
- **raw dict 병합.** live ``topics.yaml`` 에는 StudyTopic 스키마 밖의 운영 키
  (``source_count``/``refresh_requested_at``/``title`` 등)가 존재한다. StudyTopic
  round-trip 만 쓰면 그 키들이 유실되므로, :func:`merge_registry_into_raw_topics`
  가 evolution 이 소유한 키만 raw dict 에 갱신하고 나머지는 보존한다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime

from simpleclaw.study.interest_signals import InterestSignal
from simpleclaw.study.source_planner import TopicKind
from simpleclaw.study.topic_registry import (
    EvolvingTopicRegistry,
    NowFn,
    SignalSource,
    Topic,
    TopicEvolutionPolicy,
    TopicSignal,
    TopicState,
)
from simpleclaw.study.types import StudyTopic

# 한국어 관심 구문 → stable ascii id 매핑. 일반 음역 대신 자주 나오는 구문만
# 소수 유지한다(그 외 한국어-only 구문은 해시 폴백으로 결정적 id 를 얻는다).
_KOREAN_HINTS: dict[str, str] = {
    "한국 증시 코스피": "korean-market-kospi",
    "한국 증시": "korean-market",
    "코스피": "kospi",
}

# category 휴리스틱 토큰. 단어 경계 매칭으로 "said"/"email" 같은 오탐을 막는다.
_MARKET_TOKENS_RE = re.compile(
    r"\b(stock|stocks|market|markets|kospi|kosdaq|nasdaq|etf)\b"
)
_AI_TOKENS_RE = re.compile(
    r"\b(ai|llm|agent|agents|codex|claude|gpt|openai|anthropic|gemini)\b"
)
_MARKET_TOKENS_KO = ("증시", "주식", "코스피", "코스닥", "환율")
_AI_TOKENS_KO = ("인공지능", "에이전트")

# source_signals 감사 기록은 최근 것만 유지한다(무한 성장 방지).
_MAX_SOURCE_SIGNALS = 20

# 자주 갱신이 필요한 category — freshness_need 를 높게 준다.
_HIGH_FRESHNESS_CATEGORIES = frozenset({"markets", "ai-industry"})

# 사용자와 결부된 관심으로 취급하는 InterestSignal.source 값.
_USER_SIGNAL_SOURCES = frozenset(
    {
        "user_message",
        "accepted_user_insight",
        "active_project",
        "decision",
        "cluster_summary",
    }
)


@dataclass(frozen=True)
class EvolutionSummary:
    """한 evolution pass 의 결과 요약.

    daily note 의 ``Topic Evolution`` 섹션과 run summary 로깅이 소비한다.
    id 목록을 보존해 "무엇이 왜 바뀌었는지"를 감사할 수 있게 한다.
    """

    created_ids: tuple[str, ...] = ()
    updated_ids: tuple[str, ...] = ()
    promoted_ids: tuple[str, ...] = ()
    demoted_ids: tuple[str, ...] = ()
    cooled_ids: tuple[str, ...] = ()
    archived_ids: tuple[str, ...] = ()
    active_targets: int = 0

    @property
    def created(self) -> int:
        return len(self.created_ids)

    @property
    def updated(self) -> int:
        return len(self.updated_ids)

    @property
    def promoted(self) -> int:
        return len(self.promoted_ids)

    @property
    def demoted(self) -> int:
        return len(self.demoted_ids)

    @property
    def cooled(self) -> int:
        return len(self.cooled_ids)

    @property
    def archived(self) -> int:
        return len(self.archived_ids)

    @property
    def touched_ids(self) -> frozenset[str]:
        """이번 pass 에서 신호를 받은 topic id 집합(생성+갱신)."""
        return frozenset(self.created_ids) | frozenset(self.updated_ids)


def slugify_topic_id(label: str) -> str:
    """topic label/hint 를 stable id 로 변환한다.

    ascii 토큰이 있으면 kebab-case 로, 알려진 한국어 구문은 매핑으로, 그 외에는
    내용 해시로 폴백한다. ``hash()`` 는 프로세스마다 salt 가 달라 id 안정성이
    깨지므로 sha1 을 쓴다(보안 용도가 아니라 결정적 축약 용도).
    """
    cleaned = " ".join(label.split()).strip()
    if not cleaned:
        return ""
    mapped = _KOREAN_HINTS.get(cleaned)
    if mapped:
        return mapped
    ascii_text = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-").lower()
    ascii_text = re.sub(r"-+", "-", ascii_text)
    if ascii_text:
        return ascii_text[:80].strip("-")
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return f"topic-{digest}"


def _signal_source(source: str) -> SignalSource:
    """InterestSignal.source 문자열을 registry 의 SignalSource 로 매핑한다.

    사용자와 결부된 신호(user_message/메모리 승격물)는 USER, 자동 산출물
    (auto_report)은 NEWS(→ general_news kind, active 승격에 불리), 그 외
    (insight/dreaming 산출물)는 DREAMING 으로 본다.
    """
    if source in _USER_SIGNAL_SOURCES:
        return SignalSource.USER
    if source == "auto_report":
        return SignalSource.NEWS
    return SignalSource.DREAMING


def _category_for_hint(hint: str) -> str:
    """topic hint 에서 source policy category 를 휴리스틱으로 정한다."""
    text = hint.lower()
    if _MARKET_TOKENS_RE.search(text) or any(t in text for t in _MARKET_TOKENS_KO):
        return "markets"
    if _AI_TOKENS_RE.search(text) or any(t in text for t in _AI_TOKENS_KO):
        return "ai-industry"
    return "general"


def interest_signal_to_topic_signal(
    signal: InterestSignal, *, now: datetime | None = None
) -> TopicSignal:
    """InterestSignal 을 EvolvingTopicRegistry 가 받는 TopicSignal 로 변환한다.

    weight → user_interest, confidence → global_importance 로 매핑한다.
    auto_report 신호는 :func:`_signal_source` 에서 NEWS 로 분류되어 kind 가
    general_news 로 남고, interest_signals 의 가중치 상한(0.3 미만)과 합쳐져
    active 승격이 구조적으로 막힌다.
    """
    at = now or datetime.now(UTC)
    hint = signal.topic_hint.strip()
    category = _category_for_hint(hint)
    return TopicSignal(
        topic_id=slugify_topic_id(hint),
        label=hint,
        category=category,
        source=_signal_source(signal.source),
        user_interest=max(0.0, min(1.0, float(signal.weight))),
        global_importance=max(0.0, min(1.0, float(signal.confidence))),
        freshness_need=0.7 if category in _HIGH_FRESHNESS_CATEGORIES else 0.5,
        at=at,
    )


def apply_interest_signals(
    registry: EvolvingTopicRegistry,
    signals: Iterable[InterestSignal],
    *,
    now: datetime | None = None,
) -> EvolutionSummary:
    """관심 신호들을 registry 에 반영하고 생성/승격/감쇠 요약을 반환한다.

    신호 반영 후 :meth:`EvolvingTopicRegistry.evolve` 를 호출해 이번 run 에서
    신호를 받지 못한 topic 의 시간 기반 감쇠(cooling/archive)까지 함께 수행한다.
    """
    at = now or datetime.now(UTC)
    created: list[str] = []
    updated: list[str] = []
    promoted: list[str] = []

    for signal in signals:
        if not signal.topic_hint.strip():
            continue  # 빈 hint 는 후보 topic 을 만들 수 없다.
        topic_signal = interest_signal_to_topic_signal(signal, now=at)
        before = registry.get(topic_signal.topic_id)
        before_state = before.state if before is not None else None
        topic = registry.record(topic_signal)
        if topic is None:
            continue  # auto_create=False 정책에서 무시된 신규 신호.
        if before is None:
            if topic.topic_id not in created:
                created.append(topic.topic_id)
        elif topic.topic_id not in created and topic.topic_id not in updated:
            updated.append(topic.topic_id)
        if (
            before_state != TopicState.ACTIVE
            and topic.state == TopicState.ACTIVE
            and topic.topic_id not in promoted
        ):
            promoted.append(topic.topic_id)

    demoted: list[str] = []
    cooled: list[str] = []
    archived: list[str] = []
    for topic in registry.evolve():
        if topic.state == TopicState.COOLING:
            cooled.append(topic.topic_id)
        elif topic.state == TopicState.ARCHIVED:
            archived.append(topic.topic_id)
        elif topic.state == TopicState.CANDIDATE:
            demoted.append(topic.topic_id)
        elif topic.state == TopicState.ACTIVE and topic.topic_id not in promoted:
            promoted.append(topic.topic_id)

    return EvolutionSummary(
        created_ids=tuple(created),
        updated_ids=tuple(updated),
        promoted_ids=tuple(promoted),
        demoted_ids=tuple(demoted),
        cooled_ids=tuple(cooled),
        archived_ids=tuple(archived),
        active_targets=len(registry.study_targets()),
    )


# ----------------------------------------------------------------------
# 영속 StudyTopic ↔ EvolvingTopicRegistry 매핑
# ----------------------------------------------------------------------


def _parse_datetime(value: str | None, fallback: datetime) -> datetime:
    """ISO 문자열을 aware datetime 으로(실패/누락 시 fallback).

    naive 문자열은 UTC 로 간주한다 — registry 의 감쇠 계산이 aware now 와
    빼기 연산을 하므로 tz 를 통일해야 한다.
    """
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def registry_from_study_topics(
    topics: Sequence[StudyTopic],
    *,
    policy: TopicEvolutionPolicy | None = None,
    now_fn: NowFn | None = None,
) -> EvolvingTopicRegistry:
    """영속 ``StudyTopic`` 목록을 진화형 레지스트리로 적재한다.

    status 가 :class:`TopicState` 로 매핑되지 않는 topic(``paused``/``deleted``
    등 운영 전용 상태)은 registry 에 넣지 않는다 — evolution 이 모르는 상태를
    임의로 전이시키지 않기 위한 보수적 선택이며, 해당 raw 항목은
    :func:`merge_registry_into_raw_topics` 가 원본 그대로 보존한다.

    ``last_signal_at`` 이 없는 topic 은 now 로 폴백한다. 과거 신호 시각을 알 수
    없는 seed topic 을 로드 즉시 감쇠시키지 않기 위함이다(단, write-back 시점에
    이 폴백 값을 저장할지는 merge 계층이 통제한다 — 매 run 리셋 방지).
    """
    resolved_now_fn = now_fn or _default_now
    registry = EvolvingTopicRegistry(policy=policy, now_fn=resolved_now_fn)
    now = resolved_now_fn()
    for item in topics:
        try:
            state = TopicState(str(item.status or "active"))
        except ValueError:
            continue  # evolution 이 관리하지 않는 운영 상태는 건드리지 않는다.
        try:
            kind = TopicKind(str(item.kind or TopicKind.USER_INTEREST.value))
        except ValueError:
            kind = TopicKind.USER_INTEREST
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        last_studied = (
            _parse_datetime(item.last_studied_at, now) if item.last_studied_at else None
        )
        topic = Topic(
            topic_id=item.id,
            label=item.label,
            category=item.category or "general",
            kind=kind,
            state=state,
            created_at=_parse_datetime(item.created_at, now),
            updated_at=_parse_datetime(item.updated_at, now),
            last_signal_at=_parse_datetime(item.last_signal_at, now),
            user_interest=float(item.interest_score or 0.0),
            global_importance=float(item.importance_score or 0.0),
            mention_count=int(item.mention_count or 0),
            peak_score=float(item.peak_score or 0.0),
            interest_score=float(item.interest_score or 0.0),
            last_studied_at=last_studied,
            max_sources=_coerce_int(metadata.get("max_sources"), default=5),
            freshness_hours=_coerce_int(metadata.get("freshness_hours"), default=24),
        )
        registry.add_existing(topic)
    return registry


def registry_to_study_topics(
    registry: EvolvingTopicRegistry,
    *,
    existing: Sequence[StudyTopic] = (),
) -> list[StudyTopic]:
    """레지스트리 상태를 영속 ``StudyTopic`` 목록으로 직렬화한다.

    registry 가 소유하지 않는 필드(description/tags/search_queries/metadata 등)는
    ``existing`` 의 같은 id 항목에서 이어받는다 — evolution 이 운영자가 편집한
    표시/검색 메타데이터를 지우지 않게 하기 위함이다.
    """
    by_id = {topic.id: topic for topic in existing}
    result: list[StudyTopic] = []
    for topic in registry.all_topics():
        previous = by_id.get(topic.topic_id)
        result.append(
            StudyTopic(
                id=topic.topic_id,
                label=topic.label,
                description=previous.description if previous else "",
                priority=previous.priority if previous else "medium",
                status=str(topic.state),
                tags=list(previous.tags) if previous else [],
                source=previous.source if previous else "interest",
                interest_score=round(topic.interest_score, 4),
                importance_score=round(topic.global_importance, 4),
                created_at=topic.created_at.isoformat(),
                updated_at=topic.updated_at.isoformat(),
                metadata=dict(previous.metadata) if previous else {},
                category=topic.category,
                kind=str(topic.kind),
                search_queries=list(previous.search_queries) if previous else [],
                source_signals=list(previous.source_signals) if previous else [],
                mention_count=topic.mention_count,
                peak_score=round(topic.peak_score, 4),
                last_signal_at=topic.last_signal_at.isoformat(),
                last_studied_at=(
                    topic.last_studied_at.isoformat() if topic.last_studied_at else None
                ),
            )
        )
    return result


# evolution 이 소유해 기존 raw dict 에 항상 갱신하는 키.
_EVOLUTION_OWNED_KEYS = ("status", "interest_score", "peak_score", "mention_count")


def merge_registry_into_raw_topics(
    raw_topics: Sequence[dict],
    registry: EvolvingTopicRegistry,
    *,
    signals: Sequence[InterestSignal] = (),
    now: datetime | None = None,
) -> list[dict]:
    """evolution 결과를 raw ``topics.yaml`` dict 목록에 병합한다.

    기존 항목은 evolution 이 소유한 키(status/점수/언급 수/신호 시각)만 갱신하고
    나머지 키(title/source_count/refresh_requested_at/운영자 편집 필드)는 그대로
    보존한다. registry 에 없는 raw 항목(paused 등 미매핑 상태)도 원본 그대로 남긴다.
    registry 에만 있는 새 topic 은 정규 StudyTopic 스키마 + ``title`` 로 추가한다.

    Args:
        raw_topics: 현재 ``topics.yaml`` 의 raw dict 목록(변형하지 않는다).
        registry: evolution pass 를 마친 레지스트리.
        signals: 이번 pass 에 적용된 신호들 — 감사 기록(source_signals)과
            "이번 run 에 신호를 받았는지" 판정에 쓴다.
        now: 신호 시각 표기용 현재 시각.

    Returns:
        병합된 새 raw dict 목록(입력 순서 보존, 신규 topic 은 뒤에 추가).
    """
    at = now or datetime.now(UTC)
    audits_by_topic: dict[str, list[dict]] = {}
    for signal in signals:
        topic_id = slugify_topic_id(signal.topic_hint.strip())
        if not topic_id:
            continue
        audits_by_topic.setdefault(topic_id, []).append(
            {
                "source": signal.source,
                "source_ref": signal.source_ref,
                "weight": round(float(signal.weight), 4),
                "confidence": round(float(signal.confidence), 4),
                "at": at.isoformat(),
            }
        )

    merged: list[dict] = []
    seen_ids: set[str] = set()
    for raw in raw_topics:
        item = dict(raw)
        topic_id = str(item.get("id") or item.get("topic_id") or "").strip()
        topic = registry.get(topic_id) if topic_id else None
        if topic is None:
            merged.append(item)  # evolution 밖의 항목은 원본 보존.
            continue
        seen_ids.add(topic_id)
        item["status"] = str(topic.state)
        item["interest_score"] = round(topic.interest_score, 4)
        item["peak_score"] = round(topic.peak_score, 4)
        item["mention_count"] = topic.mention_count
        item["updated_at"] = topic.updated_at.isoformat()
        item["kind"] = str(topic.kind)
        if not str(item.get("category") or "").strip():
            # 운영자가 지정한 category 는 신호 휴리스틱으로 덮지 않는다.
            item["category"] = topic.category
        audits = audits_by_topic.get(topic_id, [])
        if audits or item.get("last_signal_at"):
            # last_signal_at 폴백(now)을 무신호 topic 에 쓰면 매 run 리셋되어
            # 감쇠가 영영 일어나지 않으므로, 신호를 받았거나 원래 있던 경우만 기록.
            item["last_signal_at"] = topic.last_signal_at.isoformat()
        if audits:
            existing_audits = item.get("source_signals")
            combined = list(existing_audits) if isinstance(existing_audits, list) else []
            combined.extend(audits)
            item["source_signals"] = combined[-_MAX_SOURCE_SIGNALS:]
        merged.append(item)

    for topic in registry.all_topics():
        if topic.topic_id in seen_ids or _raw_has_id(raw_topics, topic.topic_id):
            continue
        study_topic = StudyTopic(
            id=topic.topic_id,
            label=topic.label,
            status=str(topic.state),
            source="interest",
            interest_score=round(topic.interest_score, 4),
            importance_score=round(topic.global_importance, 4),
            created_at=topic.created_at.isoformat(),
            updated_at=topic.updated_at.isoformat(),
            category=topic.category,
            kind=str(topic.kind),
            source_signals=audits_by_topic.get(topic.topic_id, [])[-_MAX_SOURCE_SIGNALS:],
            mention_count=topic.mention_count,
            peak_score=round(topic.peak_score, 4),
            last_signal_at=topic.last_signal_at.isoformat(),
        )
        record = {f.name: getattr(study_topic, f.name) for f in fields(StudyTopic)}
        # 운영자 조회 도구(study_status)와 runner 가 표시명으로 title 을 읽는다.
        record["title"] = topic.label
        merged.append(record)
    return merged


def _raw_has_id(raw_topics: Sequence[dict], topic_id: str) -> bool:
    """raw 목록에 이미 해당 id 항목이 있는지(중복 추가 방지)."""
    for raw in raw_topics:
        if str(raw.get("id") or raw.get("topic_id") or "").strip() == topic_id:
            return True
    return False


def _default_now() -> datetime:
    """기본 now 제공자(UTC aware)."""
    return datetime.now(UTC)


def _coerce_int(value: object, *, default: int) -> int:
    """metadata 의 정수 필드 정규화(실패 시 default)."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
