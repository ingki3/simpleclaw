"""운영자용 verification evidence ledger 조작 도구 (BIZ-441).

``verification_evidence`` native tool 의 Function Calling 핸들러. Multica /
Review Agent / Hermes 가 PR CI, release CI, deploy/restart, health smoke,
product-intent 확인 결과를 issue 단위 구조화 evidence 로 저장(record)하고,
required stage 목록에 대한 done 허용/차단 판정(done_allowed)과 stage 별 요약
(summary)을 조회한다.

## 설계 결정

- **operator scope 강제.** tool registry 에서 OPERATOR scope + operator_gate 로
  노출하고, dispatch 에서도 operator_tools 가 아니면 차단한다. 일반 사용자
  runtime tool 표면에는 절대 노출하지 않는다.
- **issue 상태 불변.** 이 핸들러는 done 허용 여부와 근거만 반환한다 — 실제
  issue done 전환/merge/deploy 는 상위 운영 흐름(Multica)의 몫이다.
- **subagent gate 는 스냅샷으로 기록.** ``record`` + ``from_subagent_gate`` 는
  BIZ-440 subagent ledger 의 required gate 상태를 읽어 ``subagent_gate`` stage
  evidence 로 저장한다 — required gate 미완료가 done block evidence 로
  표현된다.
- **config 는 호출 시점 해석.** ledger 경로/보존 기간은 매 호출 config.yaml 을
  다시 읽는다(review_subagent_ledger 와 동일) — Hot 반영, 재시작 불필요.
- **redaction 은 ledger 저장 계층이 강제.** 핸들러는 raw_excerpt 를 그대로
  넘기고, 시크릿 마스킹/길이 제한은 ``VerificationEvidenceLedger.record`` 가
  항상 수행한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simpleclaw.config_sections.review import _REVIEW_DEFAULTS, load_review_config
from simpleclaw.review.subagent_ledger import SubagentReviewLedger
from simpleclaw.review.verification_ledger import (
    NowFn,
    VerificationEvidenceLedger,
    VerificationLedgerError,
    VerificationStage,
)

_VALID_ACTIONS = frozenset({"record", "list", "summary", "done_allowed"})

# list 응답 폭주 방지 상한 — stage 는 issue 당 십수 개 수준이 정상이므로
# 이보다 많으면 최근 것만 보여 준다.
_MAX_LIST_RECORDS = 50


def _build_ledger(
    config_path: str | Path | None, now: NowFn | None
) -> VerificationEvidenceLedger:
    """config.yaml 의 review.verification_ledger 설정으로 ledger 를 만든다."""
    if config_path is not None:
        cfg = load_review_config(config_path)
    else:
        cfg = {
            "verification_ledger": dict(_REVIEW_DEFAULTS["verification_ledger"])
        }
    ledger_cfg = cfg["verification_ledger"]
    return VerificationEvidenceLedger(
        ledger_cfg["path"],
        retention_days=ledger_cfg["retention_days"],
        now=now,
    )


def _build_subagent_ledger(
    config_path: str | Path | None,
) -> SubagentReviewLedger:
    """from_subagent_gate 스냅샷용 subagent ledger 를 config 로 만든다."""
    if config_path is not None:
        cfg = load_review_config(config_path)
    else:
        cfg = {"subagent_ledger": dict(_REVIEW_DEFAULTS["subagent_ledger"])}
    ledger_cfg = cfg["subagent_ledger"]
    return SubagentReviewLedger(
        ledger_cfg["path"],
        retention_days=ledger_cfg["retention_days"],
    )


def handle_verification_evidence(
    args: dict[str, Any],
    *,
    config_path: str | Path | None = None,
    ledger: VerificationEvidenceLedger | None = None,
    subagent_ledger: SubagentReviewLedger | None = None,
    now: NowFn | None = None,
) -> str:
    """``verification_evidence`` operator native tool 핸들러.

    Args:
        args: ``action`` (record|list|summary|done_allowed) 과 action 별 필드를
            담은 tool arguments.
        config_path: ledger 경로/보존 기간 해석용 config.yaml 경로.
        ledger: 테스트 주입용 ledger. 주어지면 config_path 해석을 건너뛴다.
        subagent_ledger: 테스트 주입용 subagent ledger (from_subagent_gate 용).
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
        if action == "record":
            if subagent_ledger is None and bool(args.get("from_subagent_gate")):
                subagent_ledger = _build_subagent_ledger(config_path)
            return _handle_record(args, ledger, subagent_ledger)
        if action == "list":
            return _handle_list(args, ledger)
        if action == "summary":
            return _handle_summary(args, ledger)
        if action == "done_allowed":
            return _handle_done_allowed(args, ledger)
    except VerificationLedgerError as exc:
        return _error_json(str(exc))
    except Exception as exc:  # noqa: BLE001 — tool loop 보호용 방어적 축약.
        return _error_json(f"verification_evidence 처리 오류: {str(exc)[:200]}")

    # _VALID_ACTIONS 검증을 통과한 이상 도달 불가하지만 안전하게.
    return _error_json(f"처리되지 않은 action '{action}'.")


# ----------------------------------------------------------------------
# action 별 처리
# ----------------------------------------------------------------------


def _handle_record(
    args: dict[str, Any],
    ledger: VerificationEvidenceLedger,
    subagent_ledger: SubagentReviewLedger | None,
) -> str:
    issue_id = _require_str(args, "issue_id")

    if bool(args.get("from_subagent_gate")):
        if subagent_ledger is None:
            return _error_json(
                "from_subagent_gate 스냅샷에 필요한 subagent ledger 를 만들 수 "
                "없습니다."
            )
        gate = subagent_ledger.gate_evidence(issue_id)
        record = ledger.record(
            issue_id=issue_id,
            stage=gate["stage"],
            status=gate["status"],
            # 운영자가 부가 설명을 덧붙일 수 있게 summary 만 override 허용.
            summary=str(args.get("summary") or gate["summary"]),
            raw_excerpt=json.dumps(gate, ensure_ascii=False),
            source=gate["source"],
        )
        return _ok_json(
            {
                "action": "record",
                "record": record.to_dict(),
                "from_subagent_gate": True,
                "blocking_record_ids": gate["blocking_record_ids"],
                "message": (
                    "subagent required gate 상태를 evidence 로 기록했습니다. "
                    + (
                        "미완료 gate 가 있어 done 이 차단됩니다."
                        if gate["blocking_record_ids"]
                        else "gate 전부 완료 — done 을 막지 않습니다."
                    )
                ),
            }
        )

    stage = _require_str(args, "stage")
    status = _require_str(args, "status")
    pr_number = args.get("pr_number")
    if pr_number is not None:
        try:
            pr_number = int(pr_number)
        except (TypeError, ValueError):
            return _error_json(f"pr_number 는 정수여야 합니다 (got '{pr_number}').")

    record = ledger.record(
        issue_id=issue_id,
        stage=stage,
        status=status,
        pr_number=pr_number,
        commit_sha=_optional_str(args, "commit_sha"),
        command=str(args.get("command") or ""),
        summary=str(args.get("summary") or ""),
        raw_excerpt=str(args.get("raw_excerpt") or ""),
        source=str(args.get("source") or ""),
    )
    return _ok_json({"action": "record", "record": record.to_dict()})


def _handle_list(args: dict[str, Any], ledger: VerificationEvidenceLedger) -> str:
    issue_id = _require_str(args, "issue_id")
    records = ledger.list_by_issue(issue_id)
    truncated = len(records) > _MAX_LIST_RECORDS
    records = records[-_MAX_LIST_RECORDS:]
    return _ok_json(
        {
            "action": "list",
            "issue_id": issue_id,
            "count": len(records),
            "truncated": truncated,
            "records": [r.to_dict() for r in records],
        }
    )


def _handle_summary(args: dict[str, Any], ledger: VerificationEvidenceLedger) -> str:
    issue_id = _require_str(args, "issue_id")
    records = ledger.list_by_issue(issue_id)
    stages = {
        r.stage: {
            "status": r.status.value,
            "summary": r.summary,
            "updated_at": r.updated_at,
        }
        for r in records
    }
    counts: dict[str, int] = {}
    for r in records:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    return _ok_json(
        {
            "action": "summary",
            "issue_id": issue_id,
            "count": len(records),
            "stages": stages,
            "status_counts": counts,
        }
    )


def _handle_done_allowed(
    args: dict[str, Any], ledger: VerificationEvidenceLedger
) -> str:
    issue_id = _require_str(args, "issue_id")
    required_raw = args.get("required_stages")
    if not isinstance(required_raw, list) or not required_raw:
        known = ", ".join(s.value for s in VerificationStage)
        return _error_json(
            "done_allowed 에는 비어 있지 않은 required_stages 배열이 필요합니다 "
            f"(표준 stage: {known})."
        )
    report = ledger.done_report(issue_id, required_raw)
    blocked_reason = None
    if not report["done_allowed"]:
        parts = []
        if report["missing_stages"]:
            parts.append(f"evidence 없음: {', '.join(report['missing_stages'])}")
        if report["failed_stages"]:
            parts.append(f"failed: {', '.join(report['failed_stages'])}")
        if report["incomplete_stages"]:
            parts.append(
                f"pending/skipped: {', '.join(report['incomplete_stages'])}"
            )
        blocked_reason = "; ".join(parts)
    return _ok_json(
        {
            "action": "done_allowed",
            "issue_id": issue_id,
            **report,
            "blocked_reason": blocked_reason,
        }
    )


# ----------------------------------------------------------------------
# 인자/응답 헬퍼
# ----------------------------------------------------------------------


def _require_str(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise VerificationLedgerError(f"이 action 에는 '{key}' 가 필요합니다.")
    return value


def _optional_str(args: dict[str, Any], key: str) -> str | None:
    value = str(args.get(key) or "").strip()
    return value or None


def _ok_json(payload: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, sort_keys=True)


def _error_json(message: str) -> str:
    return json.dumps(
        {"ok": False, "error": message}, ensure_ascii=False, sort_keys=True
    )


__all__ = ["handle_verification_evidence"]
