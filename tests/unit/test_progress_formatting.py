"""BIZ-425 — complex fact progress 의 사용자 친화 한국어 표시 검증.

내부 슬롯 이름(current_state 등)과 영어 slot question 이 Telegram progress
줄에 그대로 노출되면 안 된다. 상세 slot/question 은 로그에만 남긴다.
"""

from __future__ import annotations

from simpleclaw.agent.progress import ProgressEvent, format_progress_line


def test_complex_fact_progress_is_user_friendly_korean():
    line = format_progress_line(
        ProgressEvent(
            "complex_fact",
            "current_state",
            "start",
            {"question": "What is the current/latest state relevant to the user's question?"},
        )
    )
    assert "current/latest" not in line
    assert "current_state" not in line
    assert "최신 상태" in line or "현재 상태" in line
    assert "시작" in line


def test_all_known_complex_fact_slots_have_korean_labels():
    slots = {
        "current_state": "최신 상태 확인",
        "comparison_set": "비교 대상 확인",
        "calculation_inputs": "계산 입력 확인",
        "decision_rules": "판정 기준 확인",
        "remaining_variables": "남은 변수 확인",
    }
    for slot, label in slots.items():
        line = format_progress_line(
            ProgressEvent("complex_fact", slot, "start", {"question": "Internal English question?"})
        )
        assert label in line
        assert slot not in line
        assert "Internal English question" not in line


def test_complex_fact_complete_keeps_status_detail():
    """question 외의 detail(status 등)은 계속 표시된다."""
    line = format_progress_line(
        ProgressEvent("complex_fact", "current_state", "complete", {"status": "final"})
    )
    assert "최신 상태 확인" in line
    assert "완료" in line
    assert "final" in line


def test_unknown_complex_fact_slot_still_hides_english_question():
    line = format_progress_line(
        ProgressEvent(
            "complex_fact",
            "novel_slot",
            "start",
            {"question": "Some internal English question?"},
        )
    )
    assert "Some internal English question" not in line


def test_non_complex_fact_kinds_are_unchanged():
    line = format_progress_line(
        ProgressEvent(kind="recipe", name="daily", status="complete", detail="2 steps")
    )
    assert line == "📋 daily 완료 — 2 steps"
