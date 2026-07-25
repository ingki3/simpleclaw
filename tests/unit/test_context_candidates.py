"""ID 기반 Planner 문맥 후보의 예산·순서·신뢰 계약을 검증한다."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from simpleclaw.agent.context_candidates import (
    ContextCandidateBuilder,
    ContextTrust,
)
from simpleclaw.memory.models import ConversationMessage, MessageRole


def _message(
    content: str,
    *,
    role: MessageRole = MessageRole.USER,
    offset: int = 0,
) -> ConversationMessage:
    return ConversationMessage(
        role=role,
        content=content,
        timestamp=datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)
        + timedelta(minutes=offset),
    )


def test_builder_uses_stable_row_ids_and_preserves_chronological_order():
    rows = [
        (41, _message("first", offset=1)),
        (57, _message("second", offset=2)),
    ]

    candidates = ContextCandidateBuilder().build(rows).candidates

    assert [candidate.turn_id for candidate in candidates] == ["msg:41", "msg:57"]
    assert [candidate.content for candidate in candidates] == ["first", "second"]


def test_builder_keeps_only_the_newest_max_turns():
    rows = [(row_id, _message(f"turn {row_id}")) for row_id in range(1, 5)]

    result = ContextCandidateBuilder(max_turns=2).build(rows)

    assert [candidate.turn_id for candidate in result.candidates] == [
        "msg:3",
        "msg:4",
    ]
    assert result.truncated is True


def test_builder_applies_total_and_per_turn_char_budgets_to_newest_first():
    rows = [
        (1, _message("a" * 10)),
        (2, _message("b" * 10)),
        (3, _message("c" * 10)),
    ]

    result = ContextCandidateBuilder(
        max_chars=9,
        max_chars_per_turn=5,
    ).build(rows)

    assert [(item.turn_id, item.content) for item in result.candidates] == [
        ("msg:2", "b" * 4),
        ("msg:3", "c" * 5),
    ]
    assert result.total_chars == 9
    assert result.truncated is True


def test_builder_normalizes_whitespace_and_excludes_empty_messages():
    rows = [
        (1, _message(" \n\t ")),
        (2, _message("  hello \n  planner  ")),
    ]

    result = ContextCandidateBuilder().build(rows)

    assert [candidate.content for candidate in result.candidates] == [
        "hello planner"
    ]
    assert result.total_chars == len("hello planner")
    assert result.truncated is True


def test_builder_assigns_role_specific_trust_and_never_marks_history_as_evidence():
    rows = [
        (1, _message("user", role=MessageRole.USER)),
        (2, _message("assistant", role=MessageRole.ASSISTANT)),
        (3, _message("system", role=MessageRole.SYSTEM)),
    ]

    candidates = ContextCandidateBuilder().build(rows).candidates

    assert [candidate.trust for candidate in candidates] == [
        ContextTrust.USER_INPUT,
        ContextTrust.ASSISTANT_CONTEXT_ONLY,
        ContextTrust.SYSTEM_CONTEXT_ONLY,
    ]
    assert all(candidate.evidence_eligible is False for candidate in candidates)


def test_prompt_json_has_stable_candidate_and_field_order():
    rows = [
        (7, _message("첫 질문", offset=1)),
        (8, _message("assistant reply", role=MessageRole.ASSISTANT, offset=2)),
    ]

    result = ContextCandidateBuilder().build(rows)
    first = result.to_prompt_json()

    assert result.to_prompt_json() == first
    assert [item["id"] for item in json.loads(first)] == ["msg:7", "msg:8"]
    assert first.index('"id"') < first.index('"role"') < first.index('"timestamp"')


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("max_turns", 0),
        ("max_chars", 0),
        ("max_chars_per_turn", 0),
    ],
)
def test_builder_rejects_non_positive_budgets(argument, value):
    with pytest.raises(ValueError, match=argument):
        ContextCandidateBuilder(**{argument: value})
