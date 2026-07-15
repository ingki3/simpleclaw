"""``review_subagent_ledger`` operator native tool 단위 테스트 (BIZ-440).

start/complete/mark_late/create_followup_note/list action 과 dispatch operator
gate 차단을 검증한다. mark_late/create_followup_note 가 parent issue 상태를
변경하는 경로가 아님을 응답 계약(parent_state_changed=False)과 ledger 내용으로
확인한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from simpleclaw.agent.review_subagent_ledger_tool import (
    handle_review_subagent_ledger,
)
from simpleclaw.agent.tool_dispatch import dispatch_tool_call
from simpleclaw.llm.models import ToolCall
from simpleclaw.review.subagent_ledger import SubagentReviewLedger

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, start: datetime = _NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def ledger(tmp_path, clock) -> SubagentReviewLedger:
    return SubagentReviewLedger(tmp_path / "ledger.jsonl", now=clock)


def _call(ledger, **args) -> dict:
    return json.loads(handle_review_subagent_ledger(args, ledger=ledger))


def _start(ledger, *, gate_kind="optional", issue_id="BIZ-1", **extra) -> dict:
    out = _call(
        ledger,
        action="start",
        issue_id=issue_id,
        spawned_by="parent-agent",
        purpose="security review",
        gate_kind=gate_kind,
        **extra,
    )
    assert out["ok"] is True
    return out["record"]


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_creates_record(ledger):
    record = _start(ledger, gate_kind="required", parent_run_id="run-1")

    assert record["issue_id"] == "BIZ-1"
    assert record["gate_kind"] == "required"
    assert record["merge_blocking"] is True
    assert record["status"] == "running"
    assert record["parent_run_id"] == "run-1"
    # ledger 파일에도 실제로 저장됐다.
    assert ledger.get(record["id"]) is not None


def test_start_requires_gate_kind_and_issue_id(ledger):
    out = _call(ledger, action="start", spawned_by="a", purpose="p", gate_kind="required")
    assert out["ok"] is False
    assert "issue_id" in out["error"]

    out = _call(ledger, action="start", issue_id="BIZ-1", spawned_by="a", purpose="p")
    assert out["ok"] is False
    assert "gate_kind" in out["error"]


def test_start_rejects_unknown_gate_kind(ledger):
    out = _call(
        ledger,
        action="start",
        issue_id="BIZ-1",
        spawned_by="a",
        purpose="p",
        gate_kind="mandatory",
    )
    assert out["ok"] is False
    assert "required/optional" in out["error"]


def test_start_stores_source_metadata(ledger):
    record = _start(ledger, source_metadata={"model": "gemini", "tokens": 42})

    assert record["source_metadata"] == {"model": "gemini", "tokens": 42}


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


def test_complete_stores_summary_and_severity(ledger):
    record = _start(ledger, gate_kind="required")

    out = _call(
        ledger,
        action="complete",
        record_id=record["id"],
        result_summary="no blocking findings",
        finding_severity="minor",
    )

    assert out["ok"] is True
    assert out["record"]["status"] == "completed"
    assert out["record"]["result_summary"] == "no blocking findings"
    assert out["record"]["finding_severity"] == "minor"
    # required gate 가 완료됐으므로 merge 가능해진다.
    assert out["can_merge"] is True


def test_complete_requires_record_id(ledger):
    out = _call(ledger, action="complete")
    assert out["ok"] is False
    assert "record_id" in out["error"]


# ---------------------------------------------------------------------------
# mark_late — parent 상태 불변 계약
# ---------------------------------------------------------------------------


def test_mark_late_does_not_mutate_parent_or_sibling_state(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    target = _start(ledger, gate_kind="optional", deadline_at=deadline)
    sibling = _start(ledger, gate_kind="required")
    clock.advance(hours=2)

    out = _call(ledger, action="mark_late", record_id=target["id"])

    assert out["ok"] is True
    assert out["record"]["status"] == "late"
    assert out["parent_state_changed"] is False
    assert "follow-up" in out["guidance"]
    # 다른 record(=parent 쪽 required gate 판정 근거)는 그대로다.
    assert ledger.get(sibling["id"]).to_dict() == sibling
    # required gate 는 여전히 미완료이므로 merge 판정도 변하지 않았다.
    assert ledger.can_merge("BIZ-1") is False


def test_mark_late_rejects_completed_record(ledger):
    record = _start(ledger)
    _call(ledger, action="complete", record_id=record["id"])

    out = _call(ledger, action="mark_late", record_id=record["id"])
    assert out["ok"] is False
    assert "이미 완료" in out["error"]


# ---------------------------------------------------------------------------
# create_followup_note
# ---------------------------------------------------------------------------


def test_create_followup_note_links_note_and_issue_id(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    record = _start(ledger, gate_kind="optional", deadline_at=deadline)
    clock.advance(hours=2)
    _call(ledger, action="mark_late", record_id=record["id"])

    out = _call(
        ledger,
        action="create_followup_note",
        record_id=record["id"],
        followup_issue_id="BIZ-999",
        note="late finding: cache invalidation race",
    )

    assert out["ok"] is True
    assert out["parent_state_changed"] is False
    assert out["record"]["followup_issue_id"] == "BIZ-999"
    assert out["record"]["followup_note"] == "late finding: cache invalidation race"
    # 실제 issue 생성 자동화는 하지 않는다는 안내를 포함한다.
    assert "자동화하지 않습니다" in out["guidance"]


def test_create_followup_note_requires_note_or_issue_id(ledger):
    record = _start(ledger)

    out = _call(ledger, action="create_followup_note", record_id=record["id"])
    assert out["ok"] is False
    assert "followup_issue_id 또는 note" in out["error"]


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_returns_issue_scoped_records(ledger):
    a = _start(ledger, issue_id="BIZ-1", gate_kind="required")
    b = _start(ledger, issue_id="BIZ-1", gate_kind="optional")
    _start(ledger, issue_id="BIZ-2")

    out = _call(ledger, action="list", issue_id="BIZ-1")

    assert out["ok"] is True
    assert out["count"] == 2
    assert {r["id"] for r in out["records"]} == {a["id"], b["id"]}
    assert out["can_merge"] is False
    assert out["blocking_record_ids"] == [a["id"]]


def test_list_late_only_filters_records(ledger, clock):
    deadline = (clock.current + timedelta(hours=1)).isoformat()
    late = _start(ledger, deadline_at=deadline)
    _start(ledger)  # deadline 없음 → late 아님
    clock.advance(hours=2)

    out = _call(ledger, action="list", issue_id="BIZ-1", late_only=True)

    assert out["count"] == 1
    assert out["records"][0]["id"] == late["id"]
    assert out["late_record_ids"] == [late["id"]]


def test_list_requires_issue_id(ledger):
    out = _call(ledger, action="list")
    assert out["ok"] is False
    assert "issue_id" in out["error"]


# ---------------------------------------------------------------------------
# action 검증 / config 해석
# ---------------------------------------------------------------------------


def test_unknown_action_is_rejected(ledger):
    out = _call(ledger, action="destroy_everything")
    assert out["ok"] is False
    assert "알 수 없는 action" in out["error"]


def test_handler_resolves_ledger_path_from_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    ledger_path = tmp_path / "custom" / "ledger.jsonl"
    cfg.write_text(
        yaml.safe_dump(
            {"review": {"subagent_ledger": {"path": str(ledger_path)}}}
        ),
        encoding="utf-8",
    )

    out = json.loads(
        handle_review_subagent_ledger(
            {
                "action": "start",
                "issue_id": "BIZ-1",
                "spawned_by": "op",
                "purpose": "review",
                "gate_kind": "optional",
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
            id="1", name="review_subagent_ledger", arguments={"action": "list"}
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
                        "subagent_ledger": {"path": str(tmp_path / "ledger.jsonl")}
                    }
                }
            ),
            encoding="utf-8",
        )
        call = ToolCall(
            id="1",
            name="review_subagent_ledger",
            arguments={
                "action": "start",
                "issue_id": "BIZ-1",
                "spawned_by": "op",
                "purpose": "review",
                "gate_kind": "required",
            },
        )
        result = await dispatch_tool_call(
            _FakeOrchestrator(cfg),
            call,
            operator_tools=True,
        )
        out = json.loads(result)
        assert out["ok"] is True
        assert out["record"]["merge_blocking"] is True
