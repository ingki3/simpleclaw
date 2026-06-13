"""Proactive opportunity 모델/스토어 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

from simpleclaw.proactive import (
    OpportunityStatus,
    OpportunityStore,
    OpportunityType,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)


def _opportunity(key: str = "cron:daily") -> ProactiveOpportunity:
    """테스트마다 같은 형태의 pending 후보를 빠르게 만든다."""
    return ProactiveOpportunity(
        type=OpportunityType.REPEATED_REQUEST,
        title="매일 아침 리마인더",
        message_draft="반복 요청이 보여서 cron 등록을 제안합니다.",
        evidence=["어제", "오늘"],
        confidence=0.82,
        priority=3,
        cooldown_key=key,
        suggested_action=SuggestedAction(
            kind=SuggestedActionKind.CREATE_CRON,
            label="cron 만들기",
            payload={"schedule": "0 8 * * *"},
        ),
        source="unit-test",
        source_msg_ids=[1, 2],
    )


def test_model_json_round_trip() -> None:
    """모델 dict 직렬화가 enum/datetime/action을 손실 없이 복원한다."""
    original = _opportunity()
    original.expires_at = datetime.now() + timedelta(days=1)

    restored = ProactiveOpportunity.from_dict(original.to_dict())

    assert restored.id == original.id
    assert restored.type == OpportunityType.REPEATED_REQUEST
    assert restored.suggested_action.kind == SuggestedActionKind.CREATE_CRON
    assert restored.source_msg_ids == [1, 2]
    assert restored.expires_at == original.expires_at


def test_pending_upsert_by_cooldown_key_deduplicates(tmp_path) -> None:
    """같은 cooldown_key의 pending 후보는 한 row로 갱신되어 중복을 만들지 않는다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    first = store.upsert_pending_by_cooldown_key(_opportunity())
    second = _opportunity()
    second.title = "갱신된 제목"

    updated = store.upsert_pending_by_cooldown_key(second)

    rows = store.list_all()
    assert len(rows) == 1
    assert updated.id == first.id
    assert rows[0].title == "갱신된 제목"


def test_terminal_row_remains_and_new_pending_is_created(tmp_path) -> None:
    """terminal row는 audit trail로 남고 같은 key의 새 pending을 추가할 수 있다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    first = store.upsert_pending_by_cooldown_key(_opportunity())
    assert store.mark_dismissed(first.id) is not None

    new_pending = store.upsert_pending_by_cooldown_key(_opportunity())

    rows = store.list_all()
    assert len(rows) == 2
    assert first.id != new_pending.id
    assert [row.status for row in rows] == [
        OpportunityStatus.DISMISSED,
        OpportunityStatus.PENDING,
    ]
    assert store.last_terminal_for_cooldown_key("cron:daily").id == first.id


def test_expired_opportunity_is_hidden_and_marked_expired(tmp_path) -> None:
    """만료된 pending은 목록에서 제외되고 expire_old 호출 시 expired로 전환된다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    old = _opportunity()
    old.expires_at = datetime(2026, 1, 1, 8, 0)
    store.upsert_pending_by_cooldown_key(old)

    assert store.list_pending(now=datetime(2026, 1, 2, 8, 0)) == []
    assert store.expire_old(now=datetime(2026, 1, 2, 8, 0)) == 1
    assert store.list_all()[0].status == OpportunityStatus.EXPIRED


def test_count_sent_since_uses_last_presented_at(tmp_path) -> None:
    """발송 일일 예산 계산은 sent 상태와 노출 시각을 기준으로 센다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    item = store.upsert_pending_by_cooldown_key(_opportunity())
    store.mark_sent(item.id, now=datetime(2026, 6, 3, 9, 0))

    assert store.count_sent_since(datetime(2026, 6, 3, 0, 0)) == 1
    assert store.count_sent_since(datetime(2026, 6, 4, 0, 0)) == 0
