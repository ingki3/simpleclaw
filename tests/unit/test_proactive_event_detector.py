"""Proactive event hook adapter 단위 테스트."""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.daemon.models import ActionType, ExecutionStatus
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.proactive.event_detector import EventDetector
from simpleclaw.proactive.models import OpportunityType, SuggestedActionKind
from simpleclaw.proactive.store import OpportunityStore


def test_cron_failure_event_creates_remediation_opportunity(tmp_path) -> None:
    """cron failure 이벤트는 복구/검토 후보로 저장된다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    detector = EventDetector(store=store, enabled=True, cron_failure_enabled=True)

    opportunity = detector.capture_cron_event(
        event_type="failure",
        job_name="morning-brief",
        error_details="HTTP 500 from upstream",
        attempt=3,
        max_attempts=3,
    )

    assert opportunity is not None
    assert opportunity.type == OpportunityType.FAILURE_RECOVERY
    assert opportunity.suggested_action.kind == SuggestedActionKind.OPEN_REVIEW
    assert opportunity.priority >= 2
    assert opportunity.urgency >= 1
    assert opportunity.status == "pending"
    assert store.list_pending()[0].cooldown_key == "event:cron_failure:morning-brief"


def test_cron_success_and_no_notify_do_not_create_opportunity(tmp_path) -> None:
    """성공/NO_NOTIFY 이벤트는 기본값에서 proactive noise를 만들지 않는다."""
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    detector = EventDetector(store=store, enabled=True, cron_failure_enabled=True)

    assert detector.capture_cron_event(event_type="success", job_name="brief") is None
    assert detector.capture_cron_event(
        event_type="success_no_notify",
        job_name="brief",
        result_summary="[NO_NOTIFY]",
    ) is None
    assert store.list_all() == []


def test_event_detector_redacts_secret_payload_from_message_and_evidence(tmp_path) -> None:
    """event payload의 token/secret/password 값은 draft/evidence에 노출하지 않는다."""
    detector = EventDetector(
        store=OpportunityStore(tmp_path / "opportunities.jsonl"),
        enabled=True,
        cron_failure_enabled=True,
    )

    opportunity = detector.capture_cron_event(
        event_type="failure",
        job_name="secret-job",
        error_details="failed with token=abc123 password=hunter2 api_key=sk-live",
        payload={"token": "abc123", "nested": {"secret": "xyz"}},
    )

    assert opportunity is not None
    combined = "\n".join([opportunity.message_draft, *opportunity.evidence])
    assert "abc123" not in combined
    assert "hunter2" not in combined
    assert "sk-live" not in combined
    assert "xyz" not in combined
    assert "[REDACTED]" in combined


class RaisingEventDetector:
    """scheduler hook 실패 흡수 검증용 fake."""

    def capture_cron_event(self, **_kwargs):
        raise RuntimeError("event hook boom")


def test_scheduler_event_hook_failure_does_not_break_cron_execution(tmp_path, monkeypatch) -> None:
    """event hook 예외는 cron 실패 기록/반환을 깨뜨리지 않는다."""
    async def no_sleep(_seconds):
        return None

    from simpleclaw.daemon import scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "_sleep", no_sleep)
    store = DaemonStore(tmp_path / "daemon.db")
    scheduler = CronScheduler(
        store,
        AsyncIOScheduler(),
        event_detector=RaisingEventDetector(),
    )
    scheduler.add_job(
        "always-fail",
        "0 9 * * *",
        ActionType.PROMPT,
        "x",
        max_attempts=1,
        circuit_break_threshold=0,
    )

    async def fail(_job):
        raise RuntimeError("boom")

    scheduler._run_action = fail

    execution = asyncio.run(scheduler.execute_job("always-fail"))

    assert execution.status == ExecutionStatus.FAILURE
    assert "boom" in execution.error_details
