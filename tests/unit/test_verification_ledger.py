"""VerificationEvidenceLedger 단위 테스트 (BIZ-441).

evidence append/list/upsert, required stage 기반 done 허용/차단 판정,
raw_excerpt redaction/길이 제한, retention 정책을 검증한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from simpleclaw.review.verification_ledger import (
    MAX_RAW_EXCERPT_CHARS,
    VerificationEvidence,
    VerificationEvidenceLedger,
    VerificationLedgerError,
    VerificationStage,
    VerificationStatus,
    normalize_stage,
    redact_excerpt,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


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
def ledger(tmp_path, clock) -> VerificationEvidenceLedger:
    return VerificationEvidenceLedger(tmp_path / "ledger.jsonl", now=clock)


# ---------------------------------------------------------------------------
# record / list
# ---------------------------------------------------------------------------


def test_record_appends_and_lists_by_issue(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed", summary="334 passed")
    ledger.record(issue_id="BIZ-1", stage="lint", status="passed")
    ledger.record(issue_id="BIZ-2", stage="unit", status="failed")

    records = ledger.list_by_issue("BIZ-1")

    assert [r.stage for r in records] == ["unit", "lint"]
    assert records[0].status is VerificationStatus.PASSED
    assert records[0].summary == "334 passed"
    assert len(ledger.list_by_issue("BIZ-2")) == 1


def test_record_stores_pr_and_commit_metadata(ledger):
    record = ledger.record(
        issue_id="BIZ-1",
        stage=VerificationStage.PR_CI,
        status=VerificationStatus.PASSED,
        pr_number=457,
        commit_sha="abc123",
        command="gh pr checks 457",
        source="github_actions",
    )

    assert record.stage == "pr_ci"
    assert record.pr_number == 457
    assert record.commit_sha == "abc123"
    assert record.command == "gh pr checks 457"
    assert record.source == "github_actions"
    assert record.created_at is not None


def test_record_requires_issue_id(ledger):
    with pytest.raises(VerificationLedgerError, match="issue_id"):
        ledger.record(issue_id="  ", stage="unit", status="passed")


def test_record_rejects_unknown_status(ledger):
    with pytest.raises(VerificationLedgerError, match="status"):
        ledger.record(issue_id="BIZ-1", stage="unit", status="green")


def test_record_rejects_malformed_stage(ledger):
    # 대문자/하이픈 오타가 별도 stage 로 갈라져 done 판정이 어긋나면 안 된다.
    with pytest.raises(VerificationLedgerError, match="stage"):
        ledger.record(issue_id="BIZ-1", stage="PR-CI", status="passed")


def test_record_allows_custom_slug_stage(ledger):
    record = ledger.record(issue_id="BIZ-1", stage="telegram_smoke", status="passed")
    assert record.stage == "telegram_smoke"


# ---------------------------------------------------------------------------
# upsert semantics
# ---------------------------------------------------------------------------


def test_same_stage_record_upserts_keeping_created_at(ledger, clock):
    first = ledger.record(
        issue_id="BIZ-1", stage="unit", status="failed", summary="2 failed"
    )
    clock.advance(hours=1)

    second = ledger.record(
        issue_id="BIZ-1", stage="unit", status="passed", summary="334 passed"
    )

    records = ledger.list_by_issue("BIZ-1")
    assert len(records) == 1
    assert second.id == first.id
    assert second.status is VerificationStatus.PASSED
    assert second.summary == "334 passed"
    # 최초 기록 시각은 보존, 갱신 시각만 진행.
    assert second.created_at == first.created_at
    assert second.updated_at != first.updated_at


def test_upsert_is_scoped_per_issue(ledger):
    a = ledger.record(issue_id="BIZ-1", stage="unit", status="passed")
    b = ledger.record(issue_id="BIZ-2", stage="unit", status="failed")

    assert a.id != b.id
    assert ledger.get("BIZ-1", "unit").status is VerificationStatus.PASSED
    assert ledger.get("BIZ-2", "unit").status is VerificationStatus.FAILED


# ---------------------------------------------------------------------------
# done 판정
# ---------------------------------------------------------------------------


def test_done_allowed_when_all_required_stages_passed(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")
    ledger.record(issue_id="BIZ-1", stage="lint", status="passed")
    ledger.record(issue_id="BIZ-1", stage="pr_ci", status="passed")

    assert ledger.done_allowed("BIZ-1", ["unit", "lint", "pr_ci"]) is True


def test_missing_required_stage_blocks_done(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")

    report = ledger.done_report("BIZ-1", ["unit", "pr_ci", "deploy"])

    assert report["done_allowed"] is False
    assert report["missing_stages"] == ["pr_ci", "deploy"]
    assert ledger.missing_required_stages("BIZ-1", ["unit", "pr_ci", "deploy"]) == [
        "pr_ci",
        "deploy",
    ]


def test_failed_required_evidence_blocks_done(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")
    ledger.record(issue_id="BIZ-1", stage="health_smoke", status="failed")

    report = ledger.done_report("BIZ-1", ["unit", "health_smoke"])

    assert report["done_allowed"] is False
    assert report["failed_stages"] == ["health_smoke"]


def test_pending_and_skipped_required_evidence_block_done(ledger):
    ledger.record(issue_id="BIZ-1", stage="subagent_gate", status="pending")
    ledger.record(issue_id="BIZ-1", stage="deploy", status="skipped")

    report = ledger.done_report("BIZ-1", ["subagent_gate", "deploy"])

    assert report["done_allowed"] is False
    assert report["incomplete_stages"] == ["subagent_gate", "deploy"]


def test_optional_evidence_does_not_block_done(ledger):
    # required 목록에 없는 stage 는 failed 여도 done 을 막지 않는다.
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")
    ledger.record(issue_id="BIZ-1", stage="perf_bench", status="failed")

    assert ledger.done_allowed("BIZ-1", ["unit"]) is True


def test_done_report_deduplicates_required_stages(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")

    report = ledger.done_report("BIZ-1", ["unit", "unit", "lint"])

    assert report["required_stages"] == ["unit", "lint"]
    assert report["missing_stages"] == ["lint"]


# ---------------------------------------------------------------------------
# redaction / 길이 제한 (저장 계층 강제)
# ---------------------------------------------------------------------------


def test_raw_excerpt_secret_redaction_is_enforced_at_store_layer(ledger):
    record = ledger.record(
        issue_id="BIZ-1",
        stage="deploy",
        status="passed",
        raw_excerpt="deploy ok token=ghp_abcdef1234567890 api_key: sk-verysecretkey123",
    )

    assert "ghp_" not in record.raw_excerpt
    assert "sk-verysecretkey123" not in record.raw_excerpt
    assert "[REDACTED]" in record.raw_excerpt
    # 디스크에 남은 원본 라인에도 시크릿이 없어야 한다.
    on_disk = ledger.path.read_text(encoding="utf-8")
    assert "ghp_abcdef1234567890" not in on_disk


def test_command_and_summary_are_redacted_too(ledger):
    # CLI 인자로 시크릿이 섞인 명령/요약도 저장 계층에서 마스킹된다.
    record = ledger.record(
        issue_id="BIZ-1",
        stage="deploy",
        status="passed",
        command="curl -H 'authorization: Bearer abc123token456' https://x",
        summary="deployed with token=ghp_leakyleaky1234",
    )

    assert "abc123token456" not in record.command
    assert "ghp_leakyleaky1234" not in record.summary


def test_raw_excerpt_is_clipped_to_tail(ledger):
    long_output = "x" * (MAX_RAW_EXCERPT_CHARS * 2) + "FINAL_ERROR_LINE"

    record = ledger.record(
        issue_id="BIZ-1", stage="unit", status="failed", raw_excerpt=long_output
    )

    assert len(record.raw_excerpt) <= MAX_RAW_EXCERPT_CHARS + 100
    # 실패 원인은 보통 출력 끝에 있으므로 꼬리를 보존한다.
    assert record.raw_excerpt.endswith("FINAL_ERROR_LINE")
    assert record.raw_excerpt.startswith("[clipped")


def test_redact_excerpt_helper_masks_bearer_tokens():
    assert "Bearer" not in redact_excerpt("Authorization: Bearer abc.def-123")
    assert "[REDACTED]" in redact_excerpt("Authorization: Bearer abc.def-123")


# ---------------------------------------------------------------------------
# 복원/retention
# ---------------------------------------------------------------------------


def test_load_skips_malformed_lines(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")
    with ledger.path.open("a", encoding="utf-8") as f:
        f.write("{not json}\n")

    assert len(ledger.load()) == 1


def test_from_dict_normalizes_unknown_status_to_pending():
    # 알 수 없는 status 가 passed 로 복원돼 done 을 잘못 허용하면 안 된다.
    record = VerificationEvidence.from_dict(
        {"id": "a", "issue_id": "BIZ-1", "stage": "unit", "status": "verified"}
    )
    assert record.status is VerificationStatus.PENDING


def test_retention_prunes_old_terminal_records_but_keeps_pending(tmp_path, clock):
    ledger = VerificationEvidenceLedger(
        tmp_path / "ledger.jsonl", retention_days=30, now=clock
    )
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")
    ledger.record(issue_id="BIZ-1", stage="subagent_gate", status="pending")
    clock.advance(days=60)

    # 저장을 트리거하면 retention 이 적용된다.
    ledger.record(issue_id="BIZ-2", stage="unit", status="passed")

    stages = {(r.issue_id, r.stage) for r in ledger.load()}
    assert ("BIZ-1", "unit") not in stages  # terminal + 기간 초과 → 정리
    assert ("BIZ-1", "subagent_gate") in stages  # pending 은 기간 무관 보존
    assert ("BIZ-2", "unit") in stages


def test_normalize_stage_accepts_enum_and_rejects_empty():
    assert normalize_stage(VerificationStage.RELEASE_CI) == "release_ci"
    with pytest.raises(VerificationLedgerError):
        normalize_stage("")


def test_records_persist_as_jsonl(ledger):
    ledger.record(issue_id="BIZ-1", stage="unit", status="passed")

    lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["issue_id"] == "BIZ-1"
    assert parsed["status"] == "passed"
