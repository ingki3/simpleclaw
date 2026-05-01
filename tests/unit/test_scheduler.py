"""Tests for the cron scheduler."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.daemon.models import (
    ActionType,
    BackoffStrategy,
    CronJobNotFoundError,
    ExecutionStatus,
)
from simpleclaw.daemon import scheduler as scheduler_module
from simpleclaw.daemon.scheduler import CronScheduler, _compute_backoff
from simpleclaw.daemon.store import DaemonStore


class TestCronScheduler:
    @pytest.fixture
    def setup(self, tmp_path):
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        scheduler = CronScheduler(store, apscheduler)
        return store, apscheduler, scheduler

    def test_add_job(self, setup):
        _, _, scheduler = setup
        job = scheduler.add_job(
            name="test-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="Hello",
        )
        assert job.name == "test-job"
        assert job.enabled is True

    def test_list_jobs(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("job-1", "0 9 * * *", ActionType.PROMPT, "Hello")
        scheduler.add_job("job-2", "30 8 * * *", ActionType.RECIPE, "recipe.yaml")
        jobs = scheduler.list_jobs()
        assert len(jobs) == 2

    def test_get_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("find-me", "0 9 * * *", ActionType.PROMPT, "Hello")
        job = scheduler.get_job("find-me")
        assert job is not None
        assert job.name == "find-me"
        assert scheduler.get_job("nonexistent") is None

    def test_update_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("updatable", "0 9 * * *", ActionType.PROMPT, "original")
        updated = scheduler.update_job("updatable", cron_expression="30 8 * * *")
        assert updated.cron_expression == "30 8 * * *"

    def test_update_nonexistent_raises(self, setup):
        _, _, scheduler = setup
        with pytest.raises(CronJobNotFoundError):
            scheduler.update_job("nonexistent", cron_expression="* * * * *")

    def test_remove_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("remove-me", "0 9 * * *", ActionType.PROMPT, "test")
        assert scheduler.remove_job("remove-me") is True
        assert scheduler.get_job("remove-me") is None
        assert scheduler.remove_job("nonexistent") is False

    def test_enable_disable_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("toggle", "0 9 * * *", ActionType.PROMPT, "test")
        disabled = scheduler.disable_job("toggle")
        assert disabled.enabled is False
        enabled = scheduler.enable_job("toggle")
        assert enabled.enabled is True

    def test_load_persisted_jobs(self, setup):
        store, apscheduler, scheduler = setup
        scheduler.add_job("persisted-1", "0 9 * * *", ActionType.PROMPT, "test")
        scheduler.add_job("persisted-2", "0 10 * * *", ActionType.RECIPE, "recipe")

        # Create a new scheduler instance to test loading
        new_scheduler = CronScheduler(store, apscheduler)
        count = new_scheduler.load_persisted_jobs()
        assert count == 2

    @pytest.mark.asyncio
    async def test_execute_prompt_job(self, setup):
        _, _, scheduler = setup
        scheduler.add_job("exec-test", "0 9 * * *", ActionType.PROMPT, "Say hello")
        execution = await scheduler.execute_job("exec-test")
        assert execution.status.value == "success"
        assert "Prompt scheduled" in execution.result_summary

    @pytest.mark.asyncio
    async def test_execute_nonexistent_raises(self, setup):
        _, _, scheduler = setup
        with pytest.raises(CronJobNotFoundError):
            await scheduler.execute_job("nonexistent")


class TestRetryAndCircuitBreak:
    """BIZ-19: 작업별 재시도 정책과 누적 실패 시 자동 차단 동작 검증."""

    @pytest.fixture
    def setup(self, tmp_path, monkeypatch):
        # 백오프 sleep을 0초로 단축 — 테스트 시간 절약.
        async def _no_sleep(_seconds):
            return None

        monkeypatch.setattr(scheduler_module, "_sleep", _no_sleep)
        store = DaemonStore(tmp_path / "test.db")
        apscheduler = MagicMock(spec=AsyncIOScheduler)
        scheduler = CronScheduler(store, apscheduler)
        return store, apscheduler, scheduler

    def test_compute_backoff_linear(self):
        assert _compute_backoff(60, 1, BackoffStrategy.LINEAR) == 60
        assert _compute_backoff(60, 2, BackoffStrategy.LINEAR) == 120
        assert _compute_backoff(60, 3, BackoffStrategy.LINEAR) == 180

    def test_compute_backoff_exponential(self):
        assert _compute_backoff(60, 1, BackoffStrategy.EXPONENTIAL) == 60
        assert _compute_backoff(60, 2, BackoffStrategy.EXPONENTIAL) == 120
        assert _compute_backoff(60, 3, BackoffStrategy.EXPONENTIAL) == 240

    def test_compute_backoff_zero(self):
        assert _compute_backoff(0, 1, BackoffStrategy.EXPONENTIAL) == 0
        assert _compute_backoff(60, 0, BackoffStrategy.EXPONENTIAL) == 0

    def test_add_job_with_retry_policy(self, setup):
        _, _, scheduler = setup
        job = scheduler.add_job(
            name="retry-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="hello",
            max_attempts=5,
            backoff_seconds=10,
            backoff_strategy="linear",
            circuit_break_threshold=2,
        )
        assert job.max_attempts == 5
        assert job.backoff_seconds == 10
        assert job.backoff_strategy == BackoffStrategy.LINEAR
        assert job.circuit_break_threshold == 2

    def test_add_job_default_retry_policy(self, setup):
        _, _, scheduler = setup
        job = scheduler.add_job(
            "default-policy", "0 9 * * *", ActionType.PROMPT, "hi"
        )
        # 기본값: 3회/60s/exponential/threshold=5
        assert job.max_attempts == 3
        assert job.backoff_seconds == 60.0
        assert job.backoff_strategy == BackoffStrategy.EXPONENTIAL
        assert job.circuit_break_threshold == 5
        assert job.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "flaky", "0 9 * * *", ActionType.PROMPT, "hi",
            max_attempts=3, backoff_seconds=0,
        )

        calls = {"n": 0}

        async def flaky_action(_job):
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return "ok"

        scheduler._run_action = flaky_action

        execution = await scheduler.execute_job("flaky")
        assert execution.status == ExecutionStatus.SUCCESS
        assert execution.attempt == 2

        # 두 개의 실행 레코드가 남아야 함: 시도 1(실패), 시도 2(성공).
        history = store.get_executions("flaky", limit=10)
        statuses = [(e.attempt, e.status) for e in history]
        # get_executions는 최신순 — 시도 2가 먼저.
        assert (2, ExecutionStatus.SUCCESS) in statuses
        assert (1, ExecutionStatus.FAILURE) in statuses

        # 성공 후 누적 실패는 리셋되어야 함.
        job_after = store.get_job("flaky")
        assert job_after.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_retry_exhaustion_increments_consecutive_failures(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "always-fail", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=3, backoff_seconds=0,
            circuit_break_threshold=0,  # 차단 비활성으로 카운터만 검증
        )

        async def always_fail(_job):
            raise RuntimeError("boom")

        scheduler._run_action = always_fail

        execution = await scheduler.execute_job("always-fail")
        assert execution.status == ExecutionStatus.FAILURE
        assert execution.attempt == 3
        assert "boom" in execution.error_details

        history = store.get_executions("always-fail", limit=10)
        assert len(history) == 3
        assert all(e.status == ExecutionStatus.FAILURE for e in history)

        job_after = store.get_job("always-fail")
        assert job_after.consecutive_failures == 1
        assert job_after.enabled is True  # threshold=0이므로 비활성되지 않음

    @pytest.mark.asyncio
    async def test_circuit_break_disables_job_and_notifies(self, setup):
        store, apscheduler, scheduler = setup
        notifier = AsyncMock()
        scheduler.set_notifier(notifier)
        scheduler.add_job(
            "burnout", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=1, backoff_seconds=0,
            circuit_break_threshold=2,
        )

        async def always_fail(_job):
            raise RuntimeError("api down")

        scheduler._run_action = always_fail

        # 1회차 실패: 카운터=1, 아직 차단되지 않음.
        await scheduler.execute_job("burnout")
        job_after_first = store.get_job("burnout")
        assert job_after_first.consecutive_failures == 1
        assert job_after_first.enabled is True
        notifier.assert_not_called()

        # 2회차 실패: 임계값 도달 → 자동 비활성 + 알림.
        await scheduler.execute_job("burnout")
        job_after_second = store.get_job("burnout")
        assert job_after_second.consecutive_failures == 2
        assert job_after_second.enabled is False
        notifier.assert_called_once()
        args, _ = notifier.call_args
        assert args[0] == "burnout"
        assert "auto-disabled" in args[1]
        # APScheduler에서도 등록 해제되어야 함.
        apscheduler.remove_job.assert_called_with("cron_burnout")

    @pytest.mark.asyncio
    async def test_enable_job_resets_consecutive_failures(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "comeback", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=1, circuit_break_threshold=2, backoff_seconds=0,
        )

        async def fail(_job):
            raise RuntimeError("nope")

        scheduler._run_action = fail
        await scheduler.execute_job("comeback")
        # 1회 실패: 카운터=1, 아직 활성.
        assert store.get_job("comeback").consecutive_failures == 1

        # 사용자가 재활성화 → 카운터 리셋.
        reenabled = scheduler.enable_job("comeback")
        assert reenabled.enabled is True
        assert reenabled.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_max_attempts_one_means_no_retry(self, setup):
        store, _, scheduler = setup
        scheduler.add_job(
            "no-retry", "0 9 * * *", ActionType.PROMPT, "x",
            max_attempts=1,
        )

        calls = {"n": 0}

        async def fail(_job):
            calls["n"] += 1
            raise RuntimeError("once")

        scheduler._run_action = fail
        execution = await scheduler.execute_job("no-retry")
        assert calls["n"] == 1
        assert execution.attempt == 1
        assert execution.status == ExecutionStatus.FAILURE
