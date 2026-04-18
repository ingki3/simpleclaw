"""Tests for the cron scheduler."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.daemon.models import ActionType, CronJobNotFoundError
from simpleclaw.daemon.scheduler import CronScheduler
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
