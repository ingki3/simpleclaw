"""운영자용 subagent review ledger 조작 도구 (BIZ-440).

``review_subagent_ledger`` native tool 의 Function Calling 핸들러. 상위 agent 가
참고용으로 띄운 subagent/parallel review 를 required/optional gate 구분과 함께
구조화 기록하고(start/complete), deadline 을 넘긴 optional review 는 parent
issue 상태를 건드리지 않는 late/follow-up 경로(mark_late/create_followup_note)로만
처리한다.

## 설계 결정

- **operator scope 강제.** tool registry 에서 OPERATOR scope + operator_gate 로
  노출하고, dispatch 에서도 operator_tools 가 아니면 차단한다. 일반 사용자
  runtime tool 표면에는 절대 노출하지 않는다.
- **parent 상태 불변.** 이 핸들러의 어떤 action 도 issue/merge/deploy 상태를
  변경하는 API 를 호출하지 않는다 — ledger record 필드만 갱신하고, late
  optional finding 은 follow-up issue draft/note 안내만 돌려준다. 실제 issue
  생성 자동화는 운영 정책상 v1 범위 밖이다.
- **config 는 호출 시점 해석.** ledger 경로/보존 기간은 매 호출 config.yaml 을
  다시 읽는다(study_status 와 동일) — Hot 반영, 재시작 불필요.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simpleclaw.config_sections.review import _REVIEW_DEFAULTS, load_review_config
from simpleclaw.review.subagent_ledger import (
    NowFn,
    ReviewGateKind,
    SubagentLedgerError,
    SubagentReviewLedger,
)

_VALID_ACTIONS = frozenset(
    {"start", "complete", "mark_late", "create_followup_note", "list"}
)

# list 응답 폭주 방지 상한 — 이슈 하나의 review record 가 이보다 많으면 운영
# 흐름 자체가 잘못된 것이므로 최근 것만 보여 준다.
_MAX_LIST_RECORDS = 50


def _build_ledger(
    config_path: str | Path | None, now: NowFn | None
) -> SubagentReviewLedger:
    """config.yaml 의 review.subagent_ledger 설정으로 ledger 를 만든다."""
    if config_path is not None:
        cfg = load_review_config(config_path)
    else:
        cfg = {"subagent_ledger": dict(_REVIEW_DEFAULTS["subagent_ledger"])}
    ledger_cfg = cfg["subagent_ledger"]
    return SubagentReviewLedger(
        ledger_cfg["path"],
        retention_days=ledger_cfg["retention_days"],
        now=now,
    )


def handle_review_subagent_ledger(
    args: dict[str, Any],
    *,
    config_path: str | Path | None = None,
    ledger: SubagentReviewLedger | None = None,
    now: NowFn | None = None,
) -> str:
    """``review_subagent_ledger`` operator native tool 핸들러.

    Args:
        args: ``action`` (start|complete|mark_late|create_followup_note|list) 과
            action 별 필드를 담은 tool arguments.
        config_path: ledger 경로/보존 기간 해석용 config.yaml 경로.
        ledger: 테스트 주입용 ledger. 주어지면 config_path 해석을 건너뛴다.
        now: 테스트 주입용 시간 콜백.

    Returns:
        운영자 응답용 JSON 문자열. 실패는 ``{"ok": false, "error": ...}`` 로
        축약해 tool loop 를 죽이지 않는다.
    """
    action = str(args.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return _error_json(
            f"알 수 없는 action '{action}'. "
            f"{sorted(_VALID_ACTIONS)} 중 하나를 사용하세요."
        )

    if ledger is None:
        ledger = _build_ledger(config_path, now)

    try:
        if action == "start":
            return _handle_start(args, ledger)
        if action == "complete":
            return _handle_complete(args, ledger)
        if action == "mark_late":
            return _handle_mark_late(args, ledger)
        if action == "create_followup_note":
            return _handle_create_followup_note(args, ledger)
        if action == "list":
            return _handle_list(args, ledger)
    except SubagentLedgerError as exc:
        return _error_json(str(exc))
    except Exception as exc:  # noqa: BLE001 — tool loop 보호용 방어적 축약.
        return _error_json(f"review_subagent_ledger 처리 오류: {str(exc)[:200]}")

    # _VALID_ACTIONS 검증을 통과한 이상 도달 불가하지만 안전하게.
    return _error_json(f"처리되지 않은 action '{action}'.")


# ----------------------------------------------------------------------
# action 별 처리
# ----------------------------------------------------------------------


def _handle_start(args: dict[str, Any], ledger: SubagentReviewLedger) -> str:
    issue_id = _require_str(args, "issue_id")
    spawned_by = _require_str(args, "spawned_by")
    purpose = _require_str(args, "purpose")
    gate_raw = _require_str(args, "gate_kind").lower()
    try:
        gate_kind = ReviewGateKind(gate_raw)
    except ValueError:
        return _error_json(
            f"gate_kind 는 required/optional 중 하나여야 합니다 (got '{gate_raw}')."
        )

    source_metadata = args.get("source_metadata")
    if not isinstance(source_metadata, dict):
        source_metadata = {}

    record = ledger.start(
        issue_id=issue_id,
        spawned_by=spawned_by,
        purpose=purpose,
        gate_kind=gate_kind,
        parent_run_id=str(args.get("parent_run_id") or "").strip(),
        deadline_at=_optional_str(args, "deadline_at"),
        source_metadata=source_metadata,
    )
    return _ok_json(
        {
            "action": "start",
            "record": record.to_dict(),
            "message": (
                f"{gate_kind.value} review record 를 생성했습니다. "
                + (
                    "required gate 이므로 완료 전까지 merge 판정이 차단됩니다."
                    if gate_kind is ReviewGateKind.REQUIRED
                    else "optional review 이므로 merge 판정을 막지 않습니다."
                )
            ),
        }
    )


def _handle_complete(args: dict[str, Any], ledger: SubagentReviewLedger) -> str:
    record_id = _require_str(args, "record_id")
    record = ledger.complete(
        record_id,
        result_summary=str(args.get("result_summary") or ""),
        finding_severity=_optional_str(args, "finding_severity"),
    )
    return _ok_json(
        {
            "action": "complete",
            "record": record.to_dict(),
            "can_merge": ledger.can_merge(record.issue_id),
        }
    )


def _handle_mark_late(args: dict[str, Any], ledger: SubagentReviewLedger) -> str:
    record_id = _require_str(args, "record_id")
    record = ledger.mark_late(record_id)
    # 운영 정책의 핵심: late 처리는 이 record 하나의 상태 표시일 뿐이다.
    # parent issue 의 done/merge/deploy 판정은 여기서 절대 변경하지 않는다.
    guidance = (
        "parent issue 상태는 변경되지 않았습니다. finding 이 있으면 "
        "create_followup_note 로 follow-up issue draft/note 를 남기세요."
        if record.gate_kind is ReviewGateKind.OPTIONAL
        else (
            "required gate 가 late 상태입니다 — 완료 전까지 merge 판정은 "
            "계속 차단됩니다."
        )
    )
    return _ok_json(
        {
            "action": "mark_late",
            "record": record.to_dict(),
            "parent_state_changed": False,
            "guidance": guidance,
        }
    )


def _handle_create_followup_note(
    args: dict[str, Any], ledger: SubagentReviewLedger
) -> str:
    record_id = _require_str(args, "record_id")
    followup_issue_id = _optional_str(args, "followup_issue_id")
    note = _optional_str(args, "note")
    record = ledger.link_followup(
        record_id,
        followup_issue_id=followup_issue_id,
        note=note,
    )
    return _ok_json(
        {
            "action": "create_followup_note",
            "record": record.to_dict(),
            "parent_state_changed": False,
            "guidance": (
                "follow-up 정보를 record 에 연결했습니다. 실제 issue 생성은 "
                "자동화하지 않습니다(v1 정책) — 운영자가 draft/note 를 보고 "
                "직접 issue 를 만든 뒤 followup_issue_id 를 연결하세요."
            ),
        }
    )


def _handle_list(args: dict[str, Any], ledger: SubagentReviewLedger) -> str:
    issue_id = _require_str(args, "issue_id")
    records = ledger.list_by_issue(issue_id)
    late_only = bool(args.get("late_only", False))
    late_ids = {r.id for r in ledger.late_records(issue_id)}
    if late_only:
        records = [r for r in records if r.id in late_ids]
    truncated = len(records) > _MAX_LIST_RECORDS
    records = records[-_MAX_LIST_RECORDS:]
    return _ok_json(
        {
            "action": "list",
            "issue_id": issue_id,
            "count": len(records),
            "truncated": truncated,
            "can_merge": ledger.can_merge(issue_id),
            "blocking_record_ids": [r.id for r in ledger.blocking_records(issue_id)],
            "late_record_ids": sorted(late_ids),
            "records": [r.to_dict() for r in records],
        }
    )


# ----------------------------------------------------------------------
# 인자/응답 헬퍼
# ----------------------------------------------------------------------


def _require_str(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise SubagentLedgerError(f"이 action 에는 '{key}' 가 필요합니다.")
    return value


def _optional_str(args: dict[str, Any], key: str) -> str | None:
    value = str(args.get(key) or "").strip()
    return value or None


def _ok_json(payload: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, sort_keys=True)


def _error_json(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False, sort_keys=True)


__all__ = ["handle_review_subagent_ledger"]
