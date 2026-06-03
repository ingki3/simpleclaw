"""Conversation-end proactive hook 단위 테스트."""

from __future__ import annotations

import asyncio

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.proactive.conversation_detector import ConversationEndDetector
from simpleclaw.proactive.models import OpportunityType, SuggestedActionKind
from simpleclaw.proactive.store import OpportunityStore


def test_daily_morning_intent_creates_cron_suggestion(tmp_path) -> None:
    """명시적 반복 의도는 cron 제안 후보로 저장된다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    detector = ConversationEndDetector(store=store, enabled=True)

    opportunity = detector.capture(
        user_text="이거 매일 아침 확인해야겠네",
        assistant_text="좋아요. 필요하면 자동화할 수 있어요.",
        source_msg_ids=[42],
    )

    assert opportunity is not None
    assert opportunity.type == OpportunityType.REPEATED_REQUEST
    assert opportunity.suggested_action.kind == SuggestedActionKind.CREATE_CRON
    assert opportunity.requires_user_approval is True
    assert opportunity.status == "pending"
    assert opportunity.cooldown_key.startswith("conversation:cron:")
    assert store.list_pending()[0].id == opportunity.id


def test_tomorrow_followup_creates_requested_followup(tmp_path) -> None:
    """명시적 미래 follow-up 요청은 후속 확인 후보로 저장된다."""
    detector = ConversationEndDetector(
        store=OpportunityStore(tmp_path / "opportunities.jsonl"),
        enabled=True,
    )

    opportunity = detector.capture(
        user_text="내일 다시 확인해줘",
        assistant_text="알겠습니다.",
        source_msg_ids=[7],
    )

    assert opportunity is not None
    assert opportunity.type == OpportunityType.REQUESTED_FOLLOWUP
    assert opportunity.suggested_action.kind == SuggestedActionKind.OPEN_REVIEW
    assert opportunity.source == "conversation_end"
    assert opportunity.source_msg_ids == [7]


@pytest.mark.parametrize(
    "text",
    [
        "와 진짜 웃기다 ㅋㅋ",
        "오늘 날씨 좋네",
        "고마워!",
        "그냥 생각난 말이야",
    ],
)
def test_casual_conversation_does_not_create_opportunity(tmp_path, text: str) -> None:
    """일반 대화/감탄/감사는 proactive 후보를 만들지 않는다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    detector = ConversationEndDetector(store=store, enabled=True)

    assert detector.capture(user_text=text, assistant_text="네") is None
    assert store.list_all() == []


def test_duplicate_cooldown_key_keeps_single_pending_row(tmp_path) -> None:
    """같은 명시적 intent가 중복 감지되어도 pending row는 하나만 유지된다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    detector = ConversationEndDetector(store=store, enabled=True)

    first = detector.capture(user_text="매일 아침 확인해줘", assistant_text="네")
    second = detector.capture(user_text="매일 아침 확인해줘", assistant_text="네")

    assert first is not None and second is not None
    assert first.id == second.id
    assert len(store.list_pending()) == 1


def test_detector_is_deterministic_without_llm_dependency(tmp_path) -> None:
    """대화 종료 hook은 latency path에서 LLM/router 호출 없이 휴리스틱만 사용한다."""
    detector = ConversationEndDetector(
        store=OpportunityStore(tmp_path / "opportunities.jsonl"),
        enabled=True,
        max_latency_ms=1,
    )

    opportunity = detector.capture(user_text="cron 만들자", assistant_text="가능합니다")

    assert opportunity is not None
    assert not hasattr(detector, "_router")
    assert any("deterministic" in item for item in opportunity.evidence)


class RaisingConversationDetector:
    """오케스트레이터 hook 실패 흡수 검증용 fake."""

    def capture(self, **_kwargs):
        raise RuntimeError("conversation hook boom")


def test_orchestrator_conversation_hook_failure_is_best_effort() -> None:
    """hook 예외는 사용자 응답 처리 경로로 전파되지 않는다."""
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator._conversation_end_detector = RaisingConversationDetector()

    asyncio.run(orchestrator._capture_conversation_end_opportunity("내일 알려줘", "네", [1]))
