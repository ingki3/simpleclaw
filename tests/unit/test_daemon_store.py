"""Tests for the daemon store."""

from datetime import datetime

import pytest

from simpleclaw.daemon.models import (
    ActionType,
    CronJob,
    CronJobExecution,
    ExecutionStatus,
    WaitState,
)
from simpleclaw.daemon.store import DaemonStore


class TestDaemonStore:
    @pytest.fixture
    def store(self, tmp_path):
        return DaemonStore(tmp_path / "test_daemon.db")

    # --- CronJob CRUD ---

    def test_save_and_get_job(self, store):
        job = CronJob(
            name="test-job",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="Hello world",
        )
        store.save_job(job)
        retrieved = store.get_job("test-job")
        assert retrieved is not None
        assert retrieved.name == "test-job"
        assert retrieved.cron_expression == "0 9 * * *"
        assert retrieved.action_type == ActionType.PROMPT
        assert retrieved.action_reference == "Hello world"
        assert retrieved.enabled is True

    def test_list_jobs(self, store):
        for i in range(3):
            store.save_job(CronJob(
                name=f"job-{i}",
                cron_expression="* * * * *",
                action_type=ActionType.RECIPE,
                action_reference=f"recipe-{i}",
            ))
        jobs = store.list_jobs()
        assert len(jobs) == 3

    def test_delete_job(self, store):
        store.save_job(CronJob(
            name="to-delete",
            cron_expression="0 0 * * *",
            action_type=ActionType.PROMPT,
            action_reference="test",
        ))
        assert store.delete_job("to-delete") is True
        assert store.get_job("to-delete") is None
        assert store.delete_job("nonexistent") is False

    def test_update_job(self, store):
        job = CronJob(
            name="updatable",
            cron_expression="0 9 * * *",
            action_type=ActionType.PROMPT,
            action_reference="original",
        )
        store.save_job(job)
        job.cron_expression = "30 8 * * *"
        job.action_reference = "updated"
        store.save_job(job)
        retrieved = store.get_job("updatable")
        assert retrieved.cron_expression == "30 8 * * *"
        assert retrieved.action_reference == "updated"

    # --- CronJobExecution ---

    def test_log_and_get_execution(self, store):
        store.save_job(CronJob(
            name="exec-job",
            cron_expression="* * * * *",
            action_type=ActionType.PROMPT,
            action_reference="test",
        ))
        execution = CronJobExecution(
            job_name="exec-job",
            status=ExecutionStatus.SUCCESS,
            result_summary="Done",
        )
        exec_id = store.log_execution(execution)
        assert exec_id > 0

        executions = store.get_executions("exec-job")
        assert len(executions) == 1
        assert executions[0].status == ExecutionStatus.SUCCESS

    def test_update_execution(self, store):
        execution = CronJobExecution(
            job_name="job",
            status=ExecutionStatus.RUNNING,
        )
        exec_id = store.log_execution(execution)
        store.update_execution(
            exec_id,
            status=ExecutionStatus.SUCCESS,
            finished_at=datetime.now(),
            result_summary="Completed",
        )
        executions = store.get_executions("job")
        assert executions[0].status == ExecutionStatus.SUCCESS
        assert executions[0].finished_at is not None

    # --- WaitState ---

    def test_save_and_get_wait_state(self, store):
        wait = WaitState(
            task_id="task-1",
            serialized_state='{"step": 3}',
            condition_type="callback",
            timeout_seconds=600,
        )
        store.save_wait_state(wait)
        retrieved = store.get_wait_state("task-1")
        assert retrieved is not None
        assert retrieved.task_id == "task-1"
        assert retrieved.condition_type == "callback"
        assert retrieved.resolved_at is None

    def test_get_pending_waits(self, store):
        for i in range(3):
            store.save_wait_state(WaitState(
                task_id=f"task-{i}",
                serialized_state="{}",
                condition_type="callback",
            ))
        pending = store.get_pending_waits()
        assert len(pending) == 3

    def test_resolve_wait_state(self, store):
        store.save_wait_state(WaitState(
            task_id="resolve-me",
            serialized_state="{}",
            condition_type="callback",
        ))
        store.resolve_wait_state("resolve-me", "completed")
        resolved = store.get_wait_state("resolve-me")
        assert resolved.resolution == "completed"
        assert resolved.resolved_at is not None
        pending = store.get_pending_waits()
        assert len(pending) == 0

    # --- DaemonState ---

    def test_get_set_state(self, store):
        assert store.get_state("key1") is None
        store.set_state("key1", "value1")
        assert store.get_state("key1") == "value1"
        store.set_state("key1", "value2")
        assert store.get_state("key1") == "value2"
