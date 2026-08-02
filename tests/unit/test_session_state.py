"""BIZ-523 — stable session identity and versioned pending state."""

from __future__ import annotations

from simpleclaw.agent.session_state import (
    PendingInteraction,
    SessionIdentity,
    SessionState,
)
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


def test_stable_key_is_deterministic_and_identity_scoped() -> None:
    first = SessionIdentity(
        channel="telegram",
        user_id="10",
        chat_id="20",
        thread_id="",
    )
    same = SessionIdentity(
        channel="telegram",
        user_id="10",
        chat_id="20",
    )
    other_user = SessionIdentity(
        channel="telegram",
        user_id="11",
        chat_id="20",
    )
    assert first.stable_key() == same.stable_key()
    assert first.stable_key() != other_user.stable_key()
    assert "10" not in first.stable_key()
    assert "20" not in first.stable_key()


def test_stable_key_canonical_encoding_prevents_separator_collision() -> None:
    left = SessionIdentity("telegram", "a\x1fb", "c")
    right = SessionIdentity("telegram", "a", "b\x1fc")
    assert left.stable_key() != right.stable_key()


def test_pending_interaction_round_trips_without_semantic_turn_state() -> None:
    session = SessionState(
        key="telegram:A",
        pending=PendingInteraction(
            kind="clarification",
            payload={
                "question": "어느 대회를 말씀하시나요?",
                "options": [{"label": "LPGA", "body": "LPGA"}],
            },
        ),
        last_completed_turn_id="turn-1",
    )
    restored = SessionState.from_record(session.to_record())
    assert restored == session
    assert not hasattr(restored, "domain")
    assert not hasattr(restored, "intents")


def test_pending_clarification_survives_store_reopen(tmp_path) -> None:
    db_path = tmp_path / "conversations.db"
    state = SessionState(
        key="telegram:A",
        pending=PendingInteraction(
            kind="clarification",
            payload={"question": "어느 대회인가요?", "options": []},
        ),
    )
    store = ConversationStore(db_path)
    store.save_session_state(state)
    store.close()

    restored = ConversationStore(db_path).load_session_state("telegram:A")
    assert restored is not None
    assert restored.pending is not None
    assert restored.pending.kind == "clarification"
    assert restored.pending.payload["question"] == "어느 대회인가요?"


def test_recent_messages_are_scoped_by_session(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.add_message(
        ConversationMessage(role=MessageRole.USER, content="A only"),
        session_key="telegram:A",
        turn_id="turn-A",
    )
    store.add_message(
        ConversationMessage(role=MessageRole.USER, content="B only"),
        session_key="telegram:B",
        turn_id="turn-B",
    )

    rows = store.get_recent_with_ids(session_key="telegram:A")
    assert [message.content for _, message in rows] == ["A only"]
    assert rows[0][1].turn_id == "turn-A"


def test_save_turn_updates_session_checkpoint_atomically(tmp_path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.save_turn(
        ConversationMessage(role=MessageRole.USER, content="question"),
        ConversationMessage(role=MessageRole.ASSISTANT, content="answer"),
        session_key="telegram:A",
        turn_id="turn-1",
    )
    restored = store.load_session_state("telegram:A")
    assert restored is not None
    assert restored.last_completed_turn_id == "turn-1"
    assert [
        message.content
        for message in store.get_recent(session_key="telegram:A")
    ] == ["question", "answer"]
