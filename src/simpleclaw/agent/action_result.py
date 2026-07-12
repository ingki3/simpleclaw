"""Tool-loop 실행 결과의 runtime용 구조화 ledger (BIZ-436).

이 모듈은 LLM에 전달하는 tool observation 텍스트와 별개로, runtime이 직접
해석할 수 있는 실행 결과(성공/실패/side-effect 여부)를 step 단위로 기록한다.
Gemini가 tool 실행 뒤 empty final answer(finish_reason=STOP, output_tokens=0)를
반환해도, 이미 완료된 외부 side-effect(캘린더 생성 등)를 잃지 않고 결정적으로
보고하기 위한 장치다.

설계 경계 — LangGraph 미도입 결정:
당장의 프로덕션 버그는 empty final-answer 복구이지 workflow orchestration이
아니므로, 현재 Native Function Calling loop(`ToolLoopRunner`)를 유지하고 graph
engine은 도입하지 않는다. 향후 durable checkpoint/resume, human-approval 노드,
명시적 DAG join이 필요해지면 이 ledger를 graph-style state 객체로 승격하는
것을 재검토한다.

fallback 발화 정책:
ledger 기반 fallback(`fallback_for_empty_final_from_ledger`)은 side-effect 성공이
최소 1건 확인된 경우에만 텍스트를 만든다. 실패/못 찾음/unknown-only 근거는
빈 문자열을 돌려 기존 `fallback_for_empty_final_after_tools()`가 현재 UX
(오류 보고/못 찾음/추가 질문)를 그대로 유지하게 한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

ActionStatus = Literal["success", "failure", "not_found", "unknown"]
OverallStatus = Literal["all_success", "partial_success", "all_failed", "unknown"]

# BIZ-437: error header 판정은 이 모듈이 단일 소스다. tool_loop 의
# `_tool_result_looks_like_explicit_error()` 가 `looks_like_explicit_error_header()`
# 를 재사용해 ledger 추론과 legacy empty-final fallback 이 항상 같은 기준으로
# 분류하게 한다 (tool_loop 가 이 모듈을 import 하므로 역방향 의존은 두지 않는다).
#
# "<prefix>:" 또는 단독 헤더로 시작할 때만 오류 envelope 로 인정한다.
# "Failed attempts are normal ..." / "Error rates in LLM agents ..." 처럼
# 정상 transcript/문서가 같은 단어로 문장을 시작하는 경우를 실패로 오분류하지
# 않기 위해 단순 "<prefix> " startswith 는 쓰지 않는다.
_ERROR_HEADER_PREFIXES = (
    "error",
    "failed",
    "exception",
    "traceback",
    "command failed",
    "skill failed",
    "도구 실행 실패",
)
# 콜론 없이도 실행 실패를 직접 선언하는 runtime 오류 포맷들. 자연어 문장과
# 겹치지 않는 구체적 phrase 만 열거한다 (broad keyword 판정 금지).
_ERROR_HEADER_PHRASES = (
    "error executing",  # skill_dispatch: "Error executing skill X: ..."
    "error running",
    "failed to ",  # "Failed to connect ..."
    "traceback (most recent call last)",
    "command failed ",  # "Command failed with exit code 1"
    "skill failed ",
    "도구 실행 실패",
)
_NOT_FOUND_MARKERS = (
    "0 chars",
    "0 rows",
    "0 row",
    "no rows",
    "not found",
    "no results",
    "검색 결과가 없습니다",
    "찾을 수 없습니다",
    "없습니다",
    "[]",
    "{}",
)
_EVENT_ID_RE = re.compile(r"Event ID:\s*([A-Za-z0-9_-]+)")
_RAW_PREVIEW_MAX_CHARS = 500
_FAILURE_DETAIL_MAX_CHARS = 240


@dataclass
class ActionError:
    """실패한 step 의 구조화된 오류 정보."""

    code: str = "unknown_error"
    message: str = ""


@dataclass
class ActionResult:
    """tool loop 한 step 의 runtime 해석 결과 snapshot."""

    step_id: str
    tool_name: str
    tool_call_id: str
    skill_name: str = ""
    action: str = ""
    status: ActionStatus = "unknown"
    side_effect: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    error: ActionError | None = None
    raw_preview: str = ""
    is_meta: bool = False

    @property
    def ok(self) -> bool | None:
        """성공/실패/미확정을 3분류로 반환한다 (unknown 은 None)."""
        if self.status == "success":
            return True
        if self.status in {"failure", "not_found"}:
            return False
        return None


@dataclass
class ActionResultLedger:
    """한 turn 동안의 ActionResult 누적 ledger."""

    results: list[ActionResult] = field(default_factory=list)

    def append(self, result: ActionResult) -> None:
        self.results.append(result)

    def useful_results(self) -> list[ActionResult]:
        """skill 문서 조회 같은 meta 결과를 제외한 사용자 작업 결과만 남긴다."""
        return [r for r in self.results if not r.is_meta]

    def overall_status(self) -> OverallStatus:
        """turn 전체를 all_success/partial/all_failed/unknown 으로 집계한다."""
        useful = self.useful_results()
        if not useful:
            return "unknown"
        successes = [r for r in useful if r.status == "success"]
        failures = [r for r in useful if r.status in {"failure", "not_found"}]
        unknowns = [r for r in useful if r.status == "unknown"]
        if successes and not failures and not unknowns:
            return "all_success"
        if successes and (failures or unknowns):
            return "partial_success"
        if failures and not successes and not unknowns:
            return "all_failed"
        return "unknown"


def looks_like_explicit_error_header(text: str) -> bool:
    """결과 텍스트가 명시적 오류 envelope/header 로 시작하는지 판정한다.

    첫 non-empty 줄의 header 만 본다 — 본문 중간에 인용된 error/failed 단어로
    정상 transcript 를 실패로 오분류하지 않기 위해서다. BIZ-437: header 판정도
    "<prefix>:" / 단독 prefix / 명시적 실패 phrase 로 좁혀, "Failed attempts
    are normal ..." 같은 정상 첫 문장을 오류로 넓히지 않는다.
    """
    lowered = text.strip().lower()
    if lowered.startswith('{"error"') or lowered.startswith("{'error'"):
        return True
    for line in text.splitlines()[:3]:
        header = line.strip().lower()
        if not header:
            continue
        if any(
            header == prefix or header.startswith(f"{prefix}:")
            for prefix in _ERROR_HEADER_PREFIXES
        ):
            return True
        return any(header.startswith(phrase) for phrase in _ERROR_HEADER_PHRASES)
    return False


def _looks_like_not_found(text: str) -> bool:
    """결과 텍스트가 명시적인 empty/not-found 결과인지 판정한다."""
    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return any(marker in lowered or marker in stripped for marker in _NOT_FOUND_MARKERS)


def _try_parse_action_json(text: str) -> dict[str, Any] | None:
    """스킬 stdout JSON contract(ok/action envelope)를 best-effort 파싱한다.

    향후 스킬 표준화(BIZ-436 후속)를 위한 준비 단계로, `ok` 또는 `action` 키가
    있는 JSON object 만 계약으로 인정한다.
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if not ({"ok", "action"} & set(parsed)):
        return None
    return parsed


def infer_action_result(
    *,
    step_index: int,
    tool_name: str,
    tool_call_id: str,
    arguments: dict[str, Any],
    sanitized_output: str,
) -> ActionResult:
    """ToolCall 정보와 sanitized output 에서 ActionResult 를 best-effort 추론한다.

    기존 `_dispatch_tool_call()` 의 `str` 반환 계약을 바꾸지 않기 위해, dispatch
    반환 타입 변경 대신 결과 텍스트에서 상태를 추론하는 계층을 둔다. heuristic 은
    의도적으로 좁게 유지한다 — 성공 오판(overclaim)이 실패 오판보다 위험하므로,
    plain-text 성공 인정은 calendar create 처럼 명확한 케이스에 한정한다.
    """
    raw_preview = sanitized_output.strip().replace("\n", " ")[:_RAW_PREVIEW_MAX_CHARS]
    skill_name = str(arguments.get("skill_name") or arguments.get("name") or "")
    base = ActionResult(
        step_id=f"step_{step_index}",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        skill_name=skill_name,
        raw_preview=raw_preview,
    )

    if tool_name == "skill_docs":
        base.status = "success"
        base.is_meta = True
        return base

    parsed = _try_parse_action_json(sanitized_output)
    if parsed is not None:
        ok = parsed.get("ok")
        base.status = "success" if ok is True else "failure" if ok is False else "unknown"
        base.action = str(parsed.get("action") or "")
        base.side_effect = bool(parsed.get("side_effect", False))
        data = parsed.get("data")
        if isinstance(data, dict):
            base.data = data
        if parsed.get("summary") and "summary" not in base.data:
            base.data["summary"] = parsed["summary"]
        err = parsed.get("error")
        if isinstance(err, dict):
            base.error = ActionError(
                code=str(err.get("code") or "unknown_error"),
                message=str(err.get("message") or ""),
            )
        elif isinstance(err, str) and err:
            base.error = ActionError(message=err)
        return base

    if looks_like_explicit_error_header(sanitized_output):
        base.status = "failure"
        base.error = ActionError(
            message=sanitized_output.strip().splitlines()[0][:_FAILURE_DETAIL_MAX_CHARS],
        )
        return base

    if _looks_like_not_found(sanitized_output):
        base.status = "not_found"
        return base

    # plain-text 성공 인정은 좁은 조건으로 한정한다: execute_skill +
    # google-calendar-skill + 명시적 성공 문구. broad keyword 성공 판정은
    # 인용/transcript 오분류(overclaim) 위험 때문에 금지한다.
    if tool_name == "execute_skill" and skill_name == "google-calendar-skill":
        if (
            "Event created successfully" in sanitized_output
            or "Event ID:" in sanitized_output
        ):
            base.status = "success"
            base.side_effect = True
            base.action = "calendar_event_create"
            match = _EVENT_ID_RE.search(sanitized_output)
            if match:
                base.data["event_id"] = match.group(1)
            return base

    return base


def _result_label(result: ActionResult) -> str:
    """사용자 보고 줄에 쓸 step 라벨을 summary > action > skill > tool 순으로 고른다."""
    if result.data.get("summary"):
        return str(result.data["summary"])
    if result.action:
        return result.action
    if result.skill_name:
        return result.skill_name
    return result.tool_name


def _format_success_line(result: ActionResult) -> str:
    label = _result_label(result)
    suffixes = []
    if result.data.get("event_id"):
        suffixes.append(f"ID: {result.data['event_id']}")
    suffix = f" ({', '.join(suffixes)})" if suffixes else ""
    return f"- 완료: {label}{suffix}"


def _format_failure_line(result: ActionResult) -> str:
    label = _result_label(result)
    reason = (
        result.error.message
        if result.error and result.error.message
        else result.raw_preview
    )
    return f"- 실패: {label} — {reason[:_FAILURE_DETAIL_MAX_CHARS]}"


def _format_unknown_line(result: ActionResult) -> str:
    label = _result_label(result)
    return f"- 확인 필요: {label} — {result.raw_preview[:_FAILURE_DETAIL_MAX_CHARS]}"


def fallback_for_empty_final_from_ledger(ledger: ActionResultLedger) -> str:
    """empty final answer 시 ledger 기반 결정적 fallback 텍스트를 만든다.

    side-effect 성공이 하나도 없으면 빈 문자열을 반환한다 — 그 경우 기존
    `fallback_for_empty_final_after_tools()` 경로가 오류/못 찾음/추가 질문 UX 를
    그대로 담당한다. 이미 실행된 외부 side-effect 는 절대 숨기지 않는 것이 이
    fallback 의 존재 이유다.
    """
    useful = ledger.useful_results()
    side_effect_successes = [
        r for r in useful if r.status == "success" and r.side_effect
    ]
    if not side_effect_successes:
        return ""

    successes = [r for r in useful if r.status == "success"]
    failures = [r for r in useful if r.status in {"failure", "not_found"}]
    unknowns = [r for r in useful if r.status == "unknown"]

    if not failures and not unknowns:
        lines = ["작업이 완료됐습니다.", *(_format_success_line(r) for r in successes)]
        return "\n".join(lines)

    lines = ["일부 작업은 완료됐고, 일부는 실패했거나 확인이 필요합니다."]
    lines.extend(_format_success_line(r) for r in successes)
    lines.extend(_format_failure_line(r) for r in failures)
    lines.extend(_format_unknown_line(r) for r in unknowns)
    return "\n".join(lines)
