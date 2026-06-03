"""Dreaming 시간에 대화 패턴에서 proactive 후보를 만드는 추출기.

이 모듈은 발송/실행을 하지 않고 ``ProactiveOpportunity`` 객체만 만든다. Dreaming
파이프라인은 이 결과를 OpportunityStore에 pending으로 적재하고, 실제 사용자 노출은
후속 scheduler/presenter가 TPO 정책을 통과한 뒤 담당한다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from simpleclaw.memory.models import (
    ConversationMessage,
    MessageRole,
    is_auto_trigger_channel,
)
from simpleclaw.proactive.models import (
    OpportunityType,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)


@dataclass(frozen=True)
class _RequestSignal:
    """반복 요청 판정을 위해 필요한 최소 메시지 정보."""

    msg_id: int
    message: ConversationMessage
    topic_key: str
    topic_label: str
    hour_bucket_start: int


class DreamingOpportunityExtractor:
    """Dreaming 코퍼스에서 deterministic proactive opportunity를 추출한다.

    MVP는 반복 작업 자동화 후보에 집중한다. 관심 기반 정보 후보는 noise 가능성이 높아
    기본 비활성이고, 미완 의도는 명확한 키워드가 있을 때 낮은 우선순위 후보로만 만든다.
    """

    _MARKET_RE = re.compile(
        r"(시장|주식|증시|코스피|나스닥|s&p|market|stock)", re.IGNORECASE
    )
    _SUMMARY_RE = re.compile(r"(요약|브리핑|정리|brief|summary|summar)", re.IGNORECASE)
    _AUTOMATION_RE = re.compile(r"(자동화|cron|크론|반복|매일|정기)", re.IGNORECASE)
    _UNFINISHED_RE = re.compile(
        r"(나중에|다음에|이슈로 만들|todo|투두|follow.?up|팔로우업)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        *,
        lookback_days: int = 14,
        min_occurrences: int = 5,
        time_bucket_hours: int = 2,
        interest_based_enabled: bool = False,
        min_confidence: float = 0.75,
        repeated_task_min_occurrences: int | None = None,
        repeated_task_time_bucket_hours: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """운영 config 값을 보수적으로 정규화한다."""
        self.lookback_days = max(1, int(lookback_days))
        if repeated_task_min_occurrences is not None:
            min_occurrences = repeated_task_min_occurrences
        if repeated_task_time_bucket_hours is not None:
            time_bucket_hours = repeated_task_time_bucket_hours
        self.min_occurrences = max(2, int(min_occurrences))
        self.time_bucket_hours = max(1, min(24, int(time_bucket_hours)))
        self.interest_based_enabled = bool(interest_based_enabled)
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self._now = now

    def extract(
        self,
        id_pairs: Iterable[tuple[int, ConversationMessage]],
        *,
        insights: object | None = None,
        active_projects: object | None = None,
        auto_trigger_filter: object | None = None,
    ) -> list[ProactiveOpportunity]:
        """메시지 rowid 쌍에서 proactive 후보를 만든다.

        부가 입력(insights/active_projects/filter result)은 후속 LLM/관심 기반 확장용으로
        인자를 열어 두되, MVP deterministic 반복 요청 판정에는 사용하지 않는다.
        """
        del insights, active_projects, auto_trigger_filter
        pairs = list(id_pairs)
        if not pairs:
            return []
        now = self._now or max(
            (msg.timestamp for _, msg in pairs), default=datetime.now()
        )
        cutoff = now - timedelta(days=self.lookback_days)
        signals: list[_RequestSignal] = []
        unfinished: list[tuple[int, ConversationMessage]] = []
        for msg_id, msg in pairs:
            if msg.role != MessageRole.USER:
                continue
            if msg.timestamp < cutoff:
                continue
            if is_auto_trigger_channel(msg.channel):
                continue
            topic = self._classify_repeated_topic(msg.content)
            if topic is not None:
                key, label = topic
                bucket = self._bucket_start(msg.timestamp.hour)
                signals.append(_RequestSignal(msg_id, msg, key, label, bucket))
                continue
            if self._UNFINISHED_RE.search(msg.content) or self._AUTOMATION_RE.search(
                msg.content
            ):
                unfinished.append((msg_id, msg))

        opportunities = self._extract_repeated_requests(signals)
        opportunities.extend(self._extract_unfinished_intents(unfinished))
        if self.interest_based_enabled:
            opportunities.extend(self._extract_interest_based_info(pairs, cutoff))
        return opportunities

    def _classify_repeated_topic(self, content: str) -> tuple[str, str] | None:
        """요청 문장을 낮은 cardinality의 topic key로 매핑한다."""
        text = content.strip().lower()
        if self._MARKET_RE.search(text) and self._SUMMARY_RE.search(text):
            return "market_summary", "시장 요약"
        # 반복 자동화 제안은 같은 '작업 자동화' 키로 묶되, 매우 일반적인 문장은 제외한다.
        if self._AUTOMATION_RE.search(text) and len(text) >= 8:
            return "automation_request", "반복 작업 자동화"
        return None

    def _bucket_start(self, hour: int) -> int:
        """±N시간 반복성을 비교하기 위해 하루 시간을 고정 폭 bucket으로 접는다."""
        return (int(hour) // self.time_bucket_hours) * self.time_bucket_hours

    def _extract_repeated_requests(
        self, signals: list[_RequestSignal]
    ) -> list[ProactiveOpportunity]:
        """topic+시간대별 누적 횟수가 threshold를 넘으면 cron 제안으로 변환한다."""
        grouped: dict[tuple[str, int], list[_RequestSignal]] = defaultdict(list)
        for signal in signals:
            grouped[(signal.topic_key, signal.hour_bucket_start)].append(signal)

        opportunities: list[ProactiveOpportunity] = []
        for (topic_key, bucket_start), items in grouped.items():
            if len(items) < self.min_occurrences:
                continue
            items.sort(key=lambda item: item.message.timestamp)
            topic_label = items[0].topic_label
            count = len(items)
            bucket_end = (bucket_start + self.time_bucket_hours) % 24
            msg_ids = [item.msg_id for item in items]
            confidence = min(0.95, 0.80 + (count - self.min_occurrences) * 0.05)
            if confidence < self.min_confidence:
                continue
            sample = items[-1].message.content.strip()
            opportunities.append(
                ProactiveOpportunity(
                    type=OpportunityType.REPEATED_REQUEST,
                    title=f"반복 요청 자동화 후보: {topic_label}",
                    message_draft=(
                        f"최근 {self.lookback_days}일 동안 '{topic_label}' 요청이 {count}회 반복됐어요. "
                        "비슷한 시간대에 자동 실행되는 cron으로 등록해둘까요?"
                    ),
                    evidence=[
                        f"count={count}",
                        f"lookback_days={self.lookback_days}",
                        f"hour_bucket={bucket_start:02d}:00-{bucket_end:02d}:00",
                        f"representative_msg_id={msg_ids[-1]}",
                        f"representative_text={sample[:120]}",
                    ],
                    confidence=confidence,
                    priority=3,
                    urgency=0,
                    cooldown_key=f"dreaming:repeated:{topic_key}",
                    suggested_action=SuggestedAction(
                        kind=SuggestedActionKind.CREATE_CRON,
                        label="반복 요청 cron 제안 검토",
                        payload={
                            "topic": topic_key,
                            "hour": bucket_start,
                            "timezone": "local",
                            "source": "dreaming",
                            "min_occurrences": self.min_occurrences,
                        },
                    ),
                    requires_user_approval=True,
                    source="dreaming",
                    source_msg_ids=msg_ids,
                )
            )
        opportunities.sort(
            key=lambda item: (item.confidence, len(item.source_msg_ids)), reverse=True
        )
        return opportunities

    def _extract_unfinished_intents(
        self, items: list[tuple[int, ConversationMessage]]
    ) -> list[ProactiveOpportunity]:
        """명시적 미완 의도는 후속 검토 후보로 만든다."""
        opportunities: list[ProactiveOpportunity] = []
        for msg_id, msg in items[:3]:
            text = msg.content.strip()
            if not text:
                continue
            opportunities.append(
                ProactiveOpportunity(
                    type=OpportunityType.UNFINISHED_FOLLOWUP,
                    title="미완 의도 후속 후보",
                    message_draft=f"전에 말씀하신 '{text[:60]}' 건을 이어서 정리해볼까요?",
                    evidence=[
                        f"unfinished_intent_msg_id={msg_id}",
                        f"text={text[:120]}",
                    ],
                    confidence=0.55,
                    priority=1,
                    cooldown_key=f"dreaming:unfinished:{msg_id}",
                    suggested_action=SuggestedAction(
                        kind=SuggestedActionKind.OPEN_REVIEW,
                        label="후속 의도 검토",
                        payload={"source": "dreaming", "message_id": msg_id},
                    ),
                    requires_user_approval=True,
                    source="dreaming",
                    source_msg_ids=[msg_id],
                )
            )
        return opportunities

    def _extract_interest_based_info(
        self,
        pairs: list[tuple[int, ConversationMessage]],
        cutoff: datetime,
    ) -> list[ProactiveOpportunity]:
        """관심 기반 정보 후보 자리표시자.

        외부 news/RSS 수집 없는 MVP에서는 기본 비활성이다. 설정으로 켜도 반복 요청이 아닌
        단순 관심 언급만으로는 발송 후보를 만들지 않게 빈 결과를 반환한다.
        """
        del pairs, cutoff
        return []
