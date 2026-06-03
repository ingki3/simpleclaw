"""대화 종료 시점의 명시적 proactive intent 감지기.

이 감지기는 사용자 응답 latency를 늘리지 않도록 LLM을 호출하지 않고 정규식/키워드만
사용한다. 감지 결과는 직접 cron/이슈/메시지를 만들지 않고 OpportunityStore에 pending
후보로만 적재한다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from simpleclaw.proactive.models import (
    OpportunityType,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)
from simpleclaw.proactive.store import OpportunityStore


@dataclass(frozen=True)
class _ConversationIntent:
    """휴리스틱 판정 결과를 opportunity 생성에 필요한 값으로 정규화한다."""

    kind: str
    title: str
    type: OpportunityType
    action_kind: SuggestedActionKind
    action_label: str
    priority: int
    urgency: int
    confidence: float


class ConversationEndDetector:
    """대화 종료 직후 명시적 follow-up/automation 의도만 pending 후보로 저장한다."""

    _CRON_RE = re.compile(
        r"(매일|매주|매월|아침마다|저녁마다|점심마다|정기적으로|반복|cron|크론|자동화|자동으로|스케줄)",
        re.IGNORECASE,
    )
    _FOLLOWUP_RE = re.compile(
        r"(나중에|내일|모레|다음에|다시\s*확인|알려줘|remind|리마인드|follow\s*-?up|팔로우업)",
        re.IGNORECASE,
    )
    _ISSUE_RE = re.compile(r"(이슈로\s*만들|issue\s*만들|티켓으로|todo|투두)", re.IGNORECASE)
    _CASUAL_RE = re.compile(r"^(고마워|감사|ㅋㅋ+|ㅎㅎ+|와+|오+|좋네|그냥|응|네)[!?.\s가-힣]*$", re.IGNORECASE)

    def __init__(
        self,
        *,
        store: OpportunityStore | None = None,
        enabled: bool = False,
        max_latency_ms: int = 50,
    ) -> None:
        """저장소와 fail-closed 설정을 주입한다."""
        self._store = store
        self.enabled = bool(enabled)
        self.max_latency_ms = max(1, int(max_latency_ms))

    def detect(
        self,
        *,
        user_text: str,
        assistant_text: str = "",
        source_msg_ids: list[int] | None = None,
    ) -> ProactiveOpportunity | None:
        """저장 부작용 없이 명시적 intent 하나를 opportunity 객체로 만든다."""
        if not self.enabled:
            return None
        text = (user_text or "").strip()
        if not text or self._is_casual(text):
            return None
        intent = self._classify(text)
        if intent is None:
            return None
        key_suffix = self._stable_key(text)
        evidence = [
            "deterministic=regex_keyword",
            f"matched_intent={intent.kind}",
            f"user_text={text[:160]}",
        ]
        if assistant_text:
            evidence.append(f"assistant_text={assistant_text.strip()[:120]}")
        return ProactiveOpportunity(
            type=intent.type,
            title=intent.title,
            message_draft=self._message_for(intent, text),
            evidence=evidence,
            confidence=intent.confidence,
            priority=intent.priority,
            urgency=intent.urgency,
            cooldown_key=f"conversation:{intent.kind}:{key_suffix}",
            suggested_action=SuggestedAction(
                kind=intent.action_kind,
                label=intent.action_label,
                payload={"source": "conversation_end", "intent": intent.kind, "text": text[:240]},
            ),
            requires_user_approval=True,
            source="conversation_end",
            source_msg_ids=list(source_msg_ids or []),
        )

    def capture(
        self,
        *,
        user_text: str,
        assistant_text: str = "",
        source_msg_ids: list[int] | None = None,
    ) -> ProactiveOpportunity | None:
        """감지된 후보를 pending row로 upsert하고, 감지되지 않으면 None을 반환한다."""
        opportunity = self.detect(
            user_text=user_text,
            assistant_text=assistant_text,
            source_msg_ids=source_msg_ids,
        )
        if opportunity is None:
            return None
        if self._store is None:
            return opportunity
        return self._store.upsert_pending_by_cooldown_key(opportunity)

    def _classify(self, text: str) -> _ConversationIntent | None:
        """keyword 우선순위로 cron/follow-up/issue intent를 분류한다."""
        if self._CRON_RE.search(text):
            return _ConversationIntent(
                kind="cron",
                title="대화 종료 자동화 후보",
                type=OpportunityType.REPEATED_REQUEST,
                action_kind=SuggestedActionKind.CREATE_CRON,
                action_label="cron 제안 검토",
                priority=3,
                urgency=0,
                confidence=0.82,
            )
        if self._ISSUE_RE.search(text):
            return _ConversationIntent(
                kind="issue",
                title="대화 종료 이슈화 후보",
                type=OpportunityType.REQUESTED_FOLLOWUP,
                action_kind=SuggestedActionKind.OPEN_REVIEW,
                action_label="이슈 생성 제안 검토",
                priority=2,
                urgency=0,
                confidence=0.76,
            )
        if self._FOLLOWUP_RE.search(text):
            return _ConversationIntent(
                kind="followup",
                title="요청된 후속 확인 후보",
                type=OpportunityType.REQUESTED_FOLLOWUP,
                action_kind=SuggestedActionKind.OPEN_REVIEW,
                action_label="후속 확인 제안 검토",
                priority=2,
                urgency=1,
                confidence=0.78,
            )
        return None

    def _is_casual(self, text: str) -> bool:
        """짧은 감탄/감사/잡담은 명시 intent로 오탐하지 않는다."""
        compact = text.strip()
        return len(compact) <= 20 and bool(self._CASUAL_RE.search(compact))

    def _stable_key(self, text: str) -> str:
        """동일 발화가 중복 감지될 때 같은 cooldown_key가 되도록 정규화한다."""
        normalized = re.sub(r"\s+", " ", text.strip().lower())[:160]
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]

    def _message_for(self, intent: _ConversationIntent, text: str) -> str:
        """presenter가 사용자에게 제안할 수 있는 초안 문구를 만든다."""
        if intent.kind == "cron":
            return f"방금 말씀하신 '{text[:60]}' 요청은 반복 자동화 후보로 보여요. cron 제안으로 검토할까요?"
        if intent.kind == "issue":
            return f"방금 말씀하신 '{text[:60]}' 내용을 이슈/후속 작업 후보로 남겨둘까요?"
        return f"방금 말씀하신 '{text[:60]}' 건을 나중에 다시 확인하는 후보로 남겨둘까요?"
