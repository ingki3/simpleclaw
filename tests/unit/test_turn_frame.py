"""BIZ-425 — TurnFrame 정규화(원문 보존 + 맥락 복원 + ambiguity 판정) 테스트.

BIZ-426 — TurnFrame 은 더 이상 primary 경로가 아니다. 이 테스트들은 LLM
turn analysis 비활성/실패 시의 결정적 fallback 동작 계약을 지킨다.
"""

from __future__ import annotations

from simpleclaw.agent.turn_frame import (
    TurnFrame,
    build_turn_frame,
    extract_context_candidates,
)


def test_non_followup_preserves_original_as_normalized():
    frame = build_turn_frame(
        "오늘 서울 날씨 어때?",
        recent_messages=[],
    )
    assert frame.original_text == "오늘 서울 날씨 어때?"
    assert frame.normalized_question == "오늘 서울 날씨 어때?"
    assert frame.confidence >= 0.9
    assert frame.needs_clarification is False


def test_ordinary_question_with_recent_context_is_not_rewritten():
    """자체 주제가 완결된 일반 질문은 최근 맥락이 있어도 원문 그대로 쓴다."""
    frame = build_turn_frame(
        "다음 주 부산 출장 일정 정리해줄래?",
        recent_messages=[
            {"role": "user", "content": "오늘 롯데 야구 어떻게 되었지?"},
            {"role": "assistant", "content": "롯데가 KT에 2대 4로 패했습니다."},
        ],
    )
    assert frame.normalized_question == frame.original_text
    assert frame.confidence >= 0.9
    assert frame.needs_clarification is False


def test_short_followup_uses_recent_context():
    frame = build_turn_frame(
        "그럼 현재 순위는?",
        recent_messages=[
            {"role": "user", "content": "오늘 롯데 야구 어떻게 되었지?"},
            {"role": "assistant", "content": "롯데가 KT에 2대 4로 패했습니다."},
        ],
    )
    assert "롯데" in frame.normalized_question or "KBO" in frame.normalized_question
    assert "현재 순위" in frame.normalized_question
    # 원문은 절대 덮어쓰지 않는다.
    assert frame.original_text == "그럼 현재 순위는?"
    assert frame.confidence >= 0.7
    assert frame.needs_clarification is False


def test_ambiguous_followup_requests_clarification():
    frame = build_turn_frame(
        "그거 다시 확인해줘",
        recent_messages=[
            {"role": "user", "content": "오늘 롯데 야구 어떻게 되었지?"},
            {"role": "assistant", "content": "롯데가 패했습니다."},
            {"role": "user", "content": "agent-study-daily 왜 실패했어?"},
            {"role": "assistant", "content": "레시피 timeout이 있었습니다."},
        ],
    )
    assert frame.needs_clarification is True
    assert len(frame.ambiguity_options) >= 2
    # clarify 로 갈 때는 정규화하지 않고 원문을 유지한다.
    assert frame.normalized_question == frame.original_text


def test_followup_with_own_topic_keyword_picks_matching_context():
    """follow-up 자체 주제어가 특정 맥락과 겹치면 되묻지 않고 그 맥락을 쓴다."""
    frame = build_turn_frame(
        "그럼 그 레시피 timeout은 몇 초였어?",
        recent_messages=[
            {"role": "user", "content": "오늘 롯데 야구 어떻게 되었지?"},
            {"role": "assistant", "content": "롯데가 패했습니다."},
            {"role": "user", "content": "agent-study-daily 레시피 왜 실패했어?"},
            {"role": "assistant", "content": "레시피 timeout이 있었습니다."},
        ],
    )
    assert frame.needs_clarification is False
    assert "레시피" in frame.normalized_question or "timeout" in frame.normalized_question


def test_followup_without_context_keeps_original_and_does_not_clarify():
    """복원할 맥락이 없으면 원문 그대로 진행한다(불필요한 되묻기 금지)."""
    frame = build_turn_frame("그럼 현재 순위는?", recent_messages=[])
    assert frame.normalized_question == "그럼 현재 순위는?"
    assert frame.needs_clarification is False


def test_extract_context_candidates_merges_same_topic_exchanges():
    """같은 주제로 이어진 여러 turn 은 하나의 맥락 후보로 병합된다."""
    candidates = extract_context_candidates(
        [
            {"role": "user", "content": "오늘 롯데 야구 어떻게 되었지?"},
            {"role": "assistant", "content": "롯데가 KT에 패했습니다."},
            {"role": "user", "content": "롯데 다음 경기 일정은?"},
            {"role": "assistant", "content": "롯데는 내일 사직에서 경기합니다."},
        ]
    )
    assert len(candidates) == 1
    assert "롯데" in candidates[0].keywords


def test_needs_clarification_property_thresholds():
    low = TurnFrame(
        original_text="a", normalized_question="a", confidence=0.5,
        ambiguity_options=["x", "y"],
    )
    high = TurnFrame(original_text="a", normalized_question="a", confidence=0.9)
    assert low.needs_clarification is True
    assert high.needs_clarification is False
