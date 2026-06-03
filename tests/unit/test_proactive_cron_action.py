"""승인된 proactive create_cron action 실행기를 검증한다."""

from __future__ import annotations

import pytest

from simpleclaw.daemon.models import ActionType, CronJob
from simpleclaw.proactive.actions import ProactiveActionExecutor, redact_secret_text
from simpleclaw.proactive.models import (
    OpportunityStatus,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)
from simpleclaw.proactive.store import OpportunityStore


class FakeCronScheduler:
    def __init__(self) -> None:
        self.jobs = {}
        self.add_calls = []

    def get_job(self, name):
        return self.jobs.get(name)

    def add_job(self, name, cron_expression, action_type, action_reference, **kwargs):
        self.add_calls.append((name, cron_expression, action_type, action_reference, kwargs))
        job = CronJob(
            name=name,
            cron_expression=cron_expression,
            action_type=action_type,
            action_reference=action_reference,
        )
        self.jobs[name] = job
        return job


def _store_with(opportunity, tmp_path):
    store = OpportunityStore(tmp_path / "opportunities.jsonl")
    store.save_all([opportunity])
    return store


def _cron_opportunity(**payload_overrides):
    payload = {
        "name": "proactive-morning-report",
        "cron_expression": "0 9 * * 1-5",
        "action_type": "prompt",
        "action_reference": "아침 리포트를 요약해줘",
    }
    payload.update(payload_overrides)
    return ProactiveOpportunity(
        id="cron-op",
        title="아침 리포트",
        confidence=0.9,
        suggested_action=SuggestedAction(
            kind=SuggestedActionKind.CREATE_CRON,
            label="매일 9시",
            payload=payload,
        ),
        requires_user_approval=True,
    )


def test_no_cron_side_effect_before_accept(tmp_path):
    opportunity = _cron_opportunity()
    store = _store_with(opportunity, tmp_path)
    cron = FakeCronScheduler()
    ProactiveActionExecutor(store=store, cron_scheduler=cron)

    assert cron.add_calls == []
    assert store.get("cron-op").status == OpportunityStatus.PENDING


@pytest.mark.asyncio
async def test_accept_creates_cron_job_and_marks_executed(tmp_path):
    opportunity = _cron_opportunity()
    store = _store_with(opportunity, tmp_path)
    cron = FakeCronScheduler()
    executor = ProactiveActionExecutor(store=store, cron_scheduler=cron)

    result = await executor.execute("cron-op", "accept")

    assert "등록" in result
    assert cron.add_calls == [
        (
            "proactive-morning-report",
            "0 9 * * 1-5",
            ActionType.PROMPT,
            "아침 리포트를 요약해줘",
            {},
        )
    ]
    assert store.get("cron-op").status == OpportunityStatus.EXECUTED


@pytest.mark.asyncio
async def test_accept_is_idempotent_when_same_cron_already_exists(tmp_path):
    opportunity = _cron_opportunity()
    store = _store_with(opportunity, tmp_path)
    cron = FakeCronScheduler()
    cron.add_job(
        "proactive-morning-report",
        "0 9 * * 1-5",
        ActionType.PROMPT,
        "아침 리포트를 요약해줘",
    )
    cron.add_calls.clear()
    executor = ProactiveActionExecutor(store=store, cron_scheduler=cron)

    result = await executor.execute("cron-op", "accept")

    assert "이미" in result
    assert cron.add_calls == []
    assert store.get("cron-op").status == OpportunityStatus.EXECUTED


@pytest.mark.asyncio
async def test_edit_schedule_updates_pending_action_payload(tmp_path):
    opportunity = _cron_opportunity()
    store = _store_with(opportunity, tmp_path)
    executor = ProactiveActionExecutor(store=store, cron_scheduler=FakeCronScheduler())

    result = await executor.execute(
        "cron-op", "edit_schedule", {"cron_expression": "30 8 * * 1-5"}
    )

    assert "변경" in result
    updated = store.get("cron-op")
    assert updated.status == OpportunityStatus.PENDING
    assert updated.suggested_action.payload["cron_expression"] == "30 8 * * 1-5"


@pytest.mark.asyncio
async def test_failure_marks_failed_and_redacts_secret_summary(tmp_path):
    opportunity = _cron_opportunity(action_reference="token=SECRET123")
    store = _store_with(opportunity, tmp_path)

    class FailingCron(FakeCronScheduler):
        def add_job(self, *args, **kwargs):
            raise RuntimeError("failed with token=SECRET123")

    executor = ProactiveActionExecutor(store=store, cron_scheduler=FailingCron())

    result = await executor.execute("cron-op", "accept")

    failed = store.get("cron-op")
    assert failed.status == OpportunityStatus.FAILED
    assert "SECRET123" not in result
    assert "SECRET123" not in failed.error_summary
    assert "[REDACTED]" in failed.error_summary


def test_redact_secret_text_removes_common_secret_patterns():
    assert redact_secret_text("token=abc123 api_key: xyz789") == "token=[REDACTED] api_key: [REDACTED]"
