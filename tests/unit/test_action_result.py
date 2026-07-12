"""ActionResultLedger 모델/추론/fallback 포매터 단위 테스트 (BIZ-436).

검증 범위:
- ActionResult.ok / ActionResultLedger.overall_status() 상태 집계
- ToolCall + sanitized output → ActionResult best-effort 추론(infer_action_result)
- side-effect 성공이 있을 때만 발화하는 ledger 기반 empty-final fallback 포매터
"""

from __future__ import annotations

from simpleclaw.agent.action_result import (
    ActionError,
    ActionResult,
    ActionResultLedger,
    fallback_for_empty_final_from_ledger,
    infer_action_result,
)


def test_action_result_ok_property():
    """status → ok 3분류(True/False/None) 매핑을 보장한다."""
    assert ActionResult(step_id="s1", tool_name="cli", tool_call_id="c1", status="success").ok is True
    assert ActionResult(step_id="s2", tool_name="cli", tool_call_id="c2", status="failure").ok is False
    assert ActionResult(step_id="s3", tool_name="cli", tool_call_id="c3", status="not_found").ok is False
    assert ActionResult(step_id="s4", tool_name="cli", tool_call_id="c4", status="unknown").ok is None


def test_ledger_overall_status_all_success():
    ledger = ActionResultLedger()
    ledger.append(ActionResult(step_id="s1", tool_name="execute_skill", tool_call_id="c1", status="success"))
    assert ledger.overall_status() == "all_success"


def test_ledger_overall_status_partial_success():
    ledger = ActionResultLedger()
    ledger.append(ActionResult(step_id="s1", tool_name="execute_skill", tool_call_id="c1", status="success"))
    ledger.append(ActionResult(step_id="s2", tool_name="execute_skill", tool_call_id="c2", status="failure"))
    assert ledger.overall_status() == "partial_success"


def test_ledger_overall_status_all_failed():
    ledger = ActionResultLedger()
    ledger.append(ActionResult(step_id="s1", tool_name="execute_skill", tool_call_id="c1", status="failure"))
    ledger.append(ActionResult(step_id="s2", tool_name="execute_skill", tool_call_id="c2", status="not_found"))
    assert ledger.overall_status() == "all_failed"


def test_ledger_ignores_meta_results_for_status():
    """skill_docs 같은 meta 결과는 사용자 작업 성공/실패 집계에서 제외한다."""
    ledger = ActionResultLedger()
    ledger.append(ActionResult(step_id="s1", tool_name="skill_docs", tool_call_id="c1", status="success", is_meta=True))
    assert ledger.overall_status() == "unknown"


# ── infer_action_result ──────────────────────────────────────────────


def test_infer_skill_docs_as_meta_result():
    result = infer_action_result(
        step_index=1,
        tool_name="skill_docs",
        tool_call_id="c1",
        arguments={"name": "google-calendar-skill"},
        sanitized_output="[Skill documentation for google-calendar-skill] ...",
    )

    assert result.status == "success"
    assert result.is_meta is True
    assert result.side_effect is False


def test_infer_calendar_create_success_from_plain_text():
    """calendar create plain-text 성공은 좁은 조건(tool+skill+명시 문구)에서만 인정한다."""
    output = (
        "Creating event...\n"
        "Event created successfully: https://www.google.com/calendar/event?eid=abc\n"
        "Event ID: 1l8ivhtgrt68f9h9i4n6s7f1d0"
    )

    result = infer_action_result(
        step_index=3,
        tool_name="execute_skill",
        tool_call_id="c3",
        arguments={"skill_name": "google-calendar-skill", "args": "create --summary ..."},
        sanitized_output=output,
    )

    assert result.status == "success"
    assert result.side_effect is True
    assert result.action == "calendar_event_create"
    assert result.skill_name == "google-calendar-skill"
    assert result.data["event_id"] == "1l8ivhtgrt68f9h9i4n6s7f1d0"


def test_infer_calendar_marker_without_calendar_skill_stays_unknown():
    """다른 스킬 출력에 'Event created successfully'가 인용돼도 성공으로 넓히지 않는다."""
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "summarize"},
        sanitized_output="회의록: 어제 작업에서 Event created successfully 로그를 확인했다.",
    )

    assert result.status == "unknown"
    assert result.side_effect is False


def test_infer_error_from_error_prefix():
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "google-calendar-skill"},
        sanitized_output="Error executing skill google-calendar-skill: auth failed",
    )

    assert result.status == "failure"
    assert result.error is not None
    assert "auth failed" in result.error.message


def test_infer_not_found_from_empty_output():
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "google-calendar-skill", "args": "list"},
        sanitized_output="   ",
    )

    assert result.status == "not_found"


def test_infer_unknown_for_successful_plain_text_without_contract():
    """계약 없는 일반 텍스트 결과는 성공으로 단정하지 않고 unknown으로 남긴다."""
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "summarize"},
        sanitized_output="Transcript:\nnormal extracted text",
    )

    assert result.status == "unknown"
    assert result.side_effect is False


def test_infer_transcript_with_error_words_is_not_failure():
    """본문 중간에 error/failed 단어가 있어도 헤더가 아니면 실패로 오분류하지 않는다."""
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "summarize"},
        sanitized_output=(
            "Transcript:\n"
            "라인 중간에 error 라는 단어와 failed 라는 단어가 인용된 정상 결과"
        ),
    )

    assert result.status == "unknown"


def test_infer_parses_structured_json_contract():
    """스킬이 JSON envelope을 반환하면 heuristic보다 우선해 파싱한다."""
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "google-calendar-skill"},
        sanitized_output=(
            '{"ok": true, "action": "calendar_event_create", "side_effect": true, '
            '"summary": "해비치 박민재 골프", '
            '"data": {"event_id": "evt999"}, "error": null}'
        ),
    )

    assert result.status == "success"
    assert result.side_effect is True
    assert result.action == "calendar_event_create"
    assert result.data["event_id"] == "evt999"
    assert result.data["summary"] == "해비치 박민재 골프"


def test_infer_parses_structured_json_failure():
    result = infer_action_result(
        step_index=1,
        tool_name="execute_skill",
        tool_call_id="c1",
        arguments={"skill_name": "google-calendar-skill"},
        sanitized_output=(
            '{"ok": false, "action": "calendar_event_create", "side_effect": false, '
            '"data": {}, "error": {"code": "calendar_not_found", '
            '"message": "골프 캘린더를 찾지 못했습니다."}}'
        ),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.code == "calendar_not_found"
    assert "골프 캘린더" in result.error.message


# ── fallback_for_empty_final_from_ledger ─────────────────────────────


def test_fallback_reports_calendar_success():
    ledger = ActionResultLedger()
    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="execute_skill",
        tool_call_id="c1",
        skill_name="google-calendar-skill",
        action="calendar_event_create",
        status="success",
        side_effect=True,
        data={"summary": "해비치 박민재 골프", "event_id": "evt123"},
    ))

    text = fallback_for_empty_final_from_ledger(ledger)

    assert "완료" in text
    assert "해비치 박민재 골프" in text
    assert "evt123" in text
    assert "확정" not in text


def test_fallback_reports_partial_success():
    ledger = ActionResultLedger()
    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="execute_skill",
        tool_call_id="c1",
        action="calendar_event_create",
        status="success",
        side_effect=True,
        data={"summary": "박민재 골프"},
    ))
    ledger.append(ActionResult(
        step_id="step_2",
        tool_name="execute_skill",
        tool_call_id="c2",
        action="reminder_create",
        status="failure",
        error=ActionError(code="scheduler_unavailable", message="scheduler unavailable"),
    ))

    text = fallback_for_empty_final_from_ledger(ledger)

    assert "일부" in text
    assert "박민재 골프" in text
    assert "scheduler unavailable" in text


def test_fallback_partial_success_marks_unknown_steps_for_review():
    """side-effect 성공 + unknown 결과 조합은 확인 필요 항목으로 함께 보고한다."""
    ledger = ActionResultLedger()
    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="execute_skill",
        tool_call_id="c1",
        action="calendar_event_create",
        status="success",
        side_effect=True,
        data={"summary": "박민재 골프"},
    ))
    ledger.append(ActionResult(
        step_id="step_2",
        tool_name="execute_skill",
        tool_call_id="c2",
        skill_name="summarize",
        status="unknown",
        raw_preview="Transcript: normal extracted text",
    ))

    text = fallback_for_empty_final_from_ledger(ledger)

    assert "일부" in text
    assert "박민재 골프" in text
    assert "확인 필요" in text


def test_fallback_degrades_for_failure_only_ledger():
    """실패만 있으면 빈 문자열을 돌려 기존 checked-but-failed 경로를 유지한다."""
    ledger = ActionResultLedger()
    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="execute_skill",
        tool_call_id="c1",
        action="calendar_event_create",
        status="failure",
        error=ActionError(message="auth failed"),
    ))

    assert fallback_for_empty_final_from_ledger(ledger) == ""


def test_fallback_degrades_for_unknown_only_ledger():
    """unknown-only 근거는 기존 generic fallback UX를 바꾸지 않도록 degrade한다."""
    ledger = ActionResultLedger()
    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="execute_skill",
        tool_call_id="c1",
        skill_name="summarize",
        status="unknown",
        raw_preview="Transcript: normal extracted text",
    ))

    assert fallback_for_empty_final_from_ledger(ledger) == ""


def test_fallback_degrades_for_success_without_side_effect():
    """side-effect 없는 read-only 성공은 기존 근거 요약 fallback에 맡긴다."""
    ledger = ActionResultLedger()
    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="execute_skill",
        tool_call_id="c1",
        skill_name="google-calendar-skill",
        status="success",
        side_effect=False,
    ))

    assert fallback_for_empty_final_from_ledger(ledger) == ""


def test_fallback_degrades_for_empty_or_meta_only_ledger():
    ledger = ActionResultLedger()
    assert fallback_for_empty_final_from_ledger(ledger) == ""

    ledger.append(ActionResult(
        step_id="step_1",
        tool_name="skill_docs",
        tool_call_id="c1",
        status="success",
        is_meta=True,
    ))
    assert fallback_for_empty_final_from_ledger(ledger) == ""
