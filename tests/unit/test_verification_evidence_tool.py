"""``verification_evidence`` operator native tool 단위 테스트 (BIZ-441).

record/list/summary/done_allowed action, raw_excerpt redaction/길이 제한,
subagent required gate 스냅샷(from_subagent_gate), dispatch operator gate
차단을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import yaml

from simpleclaw.agent.tool_dispatch import dispatch_tool_call
from simpleclaw.agent.verification_evidence_tool import (
    handle_verification_evidence,
)
from simpleclaw.llm.models import ToolCall
from simpleclaw.review.subagent_ledger import SubagentReviewLedger
from simpleclaw.review.verification_ledger import (
    MAX_RAW_EXCERPT_CHARS,
    VerificationEvidenceLedger,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger(tmp_path) -> VerificationEvidenceLedger:
    return VerificationEvidenceLedger(tmp_path / "ledger.jsonl", now=lambda: _NOW)


@pytest.fixture
def subagent_ledger(tmp_path) -> SubagentReviewLedger:
    return SubagentReviewLedger(
        tmp_path / "subagent_ledger.jsonl", now=lambda: _NOW
    )


def _call(ledger, subagent_ledger=None, **args) -> dict:
    return json.loads(
        handle_verification_evidence(
            args, ledger=ledger, subagent_ledger=subagent_ledger
        )
    )


def _record(ledger, *, issue_id="BIZ-1", stage="unit", status="passed", **extra) -> dict:
    out = _call(
        ledger,
        action="record",
        issue_id=issue_id,
        stage=stage,
        status=status,
        **extra,
    )
    assert out["ok"] is True
    return out["record"]


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_record_persists_evidence(ledger):
    record = _record(
        ledger,
        stage="pr_ci",
        status="passed",
        pr_number=457,
        commit_sha="abc123",
        command="gh pr checks 457",
        summary="all checks green",
        source="github_actions",
    )

    assert record["stage"] == "pr_ci"
    assert record["status"] == "passed"
    assert record["pr_number"] == 457
    assert record["commit_sha"] == "abc123"
    # ledger 파일에도 실제로 저장됐다.
    assert ledger.get("BIZ-1", "pr_ci") is not None


def test_record_requires_issue_id_stage_status(ledger):
    out = _call(ledger, action="record", stage="unit", status="passed")
    assert out["ok"] is False
    assert "issue_id" in out["error"]

    out = _call(ledger, action="record", issue_id="BIZ-1", status="passed")
    assert out["ok"] is False
    assert "stage" in out["error"]

    out = _call(ledger, action="record", issue_id="BIZ-1", stage="unit")
    assert out["ok"] is False
    assert "status" in out["error"]


def test_record_rejects_invalid_status_and_pr_number(ledger):
    out = _call(
        ledger, action="record", issue_id="BIZ-1", stage="unit", status="green"
    )
    assert out["ok"] is False
    assert "status" in out["error"]

    out = _call(
        ledger,
        action="record",
        issue_id="BIZ-1",
        stage="pr_ci",
        status="passed",
        pr_number="not-a-number",
    )
    assert out["ok"] is False
    assert "pr_number" in out["error"]


def test_record_redacts_and_clips_raw_excerpt(ledger):
    long_tail = "y" * (MAX_RAW_EXCERPT_CHARS * 2)
    record = _record(
        ledger,
        stage="deploy",
        raw_excerpt=f"token=ghp_secretsecret1234 {long_tail}",
    )

    assert "ghp_secretsecret1234" not in record["raw_excerpt"]
    assert len(record["raw_excerpt"]) <= MAX_RAW_EXCERPT_CHARS + 100
    # 디스크 원본에도 시크릿이 없어야 한다.
    assert "ghp_secretsecret1234" not in ledger.path.read_text(encoding="utf-8")


def test_record_same_stage_upserts(ledger):
    first = _record(ledger, stage="unit", status="failed")
    second = _record(ledger, stage="unit", status="passed")

    assert second["id"] == first["id"]
    out = _call(ledger, action="list", issue_id="BIZ-1")
    assert out["count"] == 1
    assert out["records"][0]["status"] == "passed"


# ---------------------------------------------------------------------------
# from_subagent_gate — required gate 미완료 = done block evidence
# ---------------------------------------------------------------------------


def test_from_subagent_gate_records_pending_when_gate_blocking(
    ledger, subagent_ledger
):
    subagent_ledger.start(
        issue_id="BIZ-1",
        spawned_by="parent-agent",
        purpose="security review",
        gate_kind="required",
    )

    out = _call(
        ledger,
        subagent_ledger,
        action="record",
        issue_id="BIZ-1",
        from_subagent_gate=True,
    )

    assert out["ok"] is True
    assert out["from_subagent_gate"] is True
    assert out["record"]["stage"] == "subagent_gate"
    assert out["record"]["status"] == "pending"
    assert out["record"]["source"] == "subagent_ledger"
    assert len(out["blocking_record_ids"]) == 1
    # required gate 미완료가 done block evidence 로 이어진다.
    done = _call(
        ledger, action="done_allowed", issue_id="BIZ-1", required_stages=["subagent_gate"]
    )
    assert done["done_allowed"] is False
    assert done["incomplete_stages"] == ["subagent_gate"]


def test_from_subagent_gate_records_passed_when_gate_clear(ledger, subagent_ledger):
    record = subagent_ledger.start(
        issue_id="BIZ-1",
        spawned_by="parent-agent",
        purpose="security review",
        gate_kind="required",
    )
    subagent_ledger.complete(record.id, result_summary="no findings")

    out = _call(
        ledger,
        subagent_ledger,
        action="record",
        issue_id="BIZ-1",
        from_subagent_gate=True,
    )

    assert out["record"]["status"] == "passed"
    assert out["blocking_record_ids"] == []
    done = _call(
        ledger, action="done_allowed", issue_id="BIZ-1", required_stages=["subagent_gate"]
    )
    assert done["done_allowed"] is True


# ---------------------------------------------------------------------------
# list / summary
# ---------------------------------------------------------------------------


def test_list_returns_issue_scoped_records(ledger):
    _record(ledger, issue_id="BIZ-1", stage="unit")
    _record(ledger, issue_id="BIZ-1", stage="lint")
    _record(ledger, issue_id="BIZ-2", stage="unit")

    out = _call(ledger, action="list", issue_id="BIZ-1")

    assert out["ok"] is True
    assert out["count"] == 2
    assert {r["stage"] for r in out["records"]} == {"unit", "lint"}
    assert out["truncated"] is False


def test_summary_reports_stage_statuses_and_counts(ledger):
    _record(ledger, stage="unit", status="passed", summary="334 passed")
    _record(ledger, stage="pr_ci", status="pending")
    _record(ledger, stage="health_smoke", status="failed")

    out = _call(ledger, action="summary", issue_id="BIZ-1")

    assert out["ok"] is True
    assert out["count"] == 3
    assert out["stages"]["unit"]["status"] == "passed"
    assert out["stages"]["unit"]["summary"] == "334 passed"
    assert out["status_counts"] == {"passed": 1, "pending": 1, "failed": 1}


# ---------------------------------------------------------------------------
# done_allowed
# ---------------------------------------------------------------------------


def test_done_allowed_blocks_until_required_stages_pass(ledger):
    _record(ledger, stage="unit", status="passed")

    out = _call(
        ledger,
        action="done_allowed",
        issue_id="BIZ-1",
        required_stages=["unit", "pr_ci"],
    )

    assert out["done_allowed"] is False
    assert out["missing_stages"] == ["pr_ci"]
    assert "pr_ci" in out["blocked_reason"]

    _record(ledger, stage="pr_ci", status="passed")
    out = _call(
        ledger,
        action="done_allowed",
        issue_id="BIZ-1",
        required_stages=["unit", "pr_ci"],
    )
    assert out["done_allowed"] is True
    assert out["blocked_reason"] is None


def test_done_allowed_reports_failed_stages(ledger):
    _record(ledger, stage="health_smoke", status="failed")

    out = _call(
        ledger,
        action="done_allowed",
        issue_id="BIZ-1",
        required_stages=["health_smoke"],
    )

    assert out["done_allowed"] is False
    assert out["failed_stages"] == ["health_smoke"]
    assert "failed" in out["blocked_reason"]


def test_done_allowed_ignores_optional_evidence(ledger):
    _record(ledger, stage="unit", status="passed")
    _record(ledger, stage="perf_bench", status="failed")

    out = _call(
        ledger, action="done_allowed", issue_id="BIZ-1", required_stages=["unit"]
    )

    assert out["done_allowed"] is True


def test_done_allowed_requires_required_stages_array(ledger):
    out = _call(ledger, action="done_allowed", issue_id="BIZ-1")
    assert out["ok"] is False
    assert "required_stages" in out["error"]

    out = _call(
        ledger, action="done_allowed", issue_id="BIZ-1", required_stages=[]
    )
    assert out["ok"] is False


# ---------------------------------------------------------------------------
# action 검증 / config 해석
# ---------------------------------------------------------------------------


def test_unknown_action_is_rejected(ledger):
    out = _call(ledger, action="mark_done")
    assert out["ok"] is False
    assert "알 수 없는 action" in out["error"]


def test_handler_resolves_ledger_path_from_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    ledger_path = tmp_path / "custom" / "evidence.jsonl"
    cfg.write_text(
        yaml.safe_dump(
            {"review": {"verification_ledger": {"path": str(ledger_path)}}}
        ),
        encoding="utf-8",
    )

    out = json.loads(
        handle_verification_evidence(
            {
                "action": "record",
                "issue_id": "BIZ-1",
                "stage": "unit",
                "status": "passed",
            },
            config_path=cfg,
        )
    )

    assert out["ok"] is True
    assert ledger_path.is_file()


# ---------------------------------------------------------------------------
# operator gate (dispatch 경계)
# ---------------------------------------------------------------------------


class _FakeOrchestrator:
    def __init__(self, config_path):
        self._config_path = config_path


class TestOperatorGate:
    @pytest.mark.asyncio
    async def test_dispatch_blocks_non_operator(self, tmp_path):
        call = ToolCall(
            id="1", name="verification_evidence", arguments={"action": "list"}
        )
        result = await dispatch_tool_call(
            _FakeOrchestrator(tmp_path / "config.yaml"),
            call,
            operator_tools=False,
        )
        assert "operator context" in result

    @pytest.mark.asyncio
    async def test_dispatch_allows_operator(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "review": {
                        "verification_ledger": {
                            "path": str(tmp_path / "evidence.jsonl")
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        call = ToolCall(
            id="1",
            name="verification_evidence",
            arguments={
                "action": "record",
                "issue_id": "BIZ-1",
                "stage": "unit",
                "status": "passed",
            },
        )
        result = await dispatch_tool_call(
            _FakeOrchestrator(cfg),
            call,
            operator_tools=True,
        )
        out = json.loads(result)
        assert out["ok"] is True
        assert out["record"]["stage"] == "unit"
