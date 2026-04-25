"""Cron scheduler: APScheduler wrapper with CRUD for cron jobs.

Includes NO_NOTIFY filtering and pluggable notification callbacks
so that all cron business logic lives in the core module.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from simpleclaw.daemon.models import (
    ActionType,
    CronJob,
    CronJobExecution,
    CronJobNotFoundError,
    ExecutionStatus,
)
from simpleclaw.daemon.store import DaemonStore

logger = logging.getLogger(__name__)

_NO_NOTIFY_TOKEN = "[NO_NOTIFY]"


class CronScheduler:
    """Manages cron jobs with APScheduler integration and SQLite persistence.

    Supports:
    - NO_NOTIFY filtering: if LLM response contains [NO_NOTIFY], skip notification
    - Pluggable notifier callback: receives cron results to send to external channels
    """

    def __init__(
        self,
        store: DaemonStore,
        apscheduler: AsyncIOScheduler,
        recipe_executor=None,
        agent_orchestrator=None,
        notifier: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._apscheduler = apscheduler
        self._recipe_executor = recipe_executor
        self._agent = agent_orchestrator
        self._notifier = notifier

    def set_notifier(
        self, notifier: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Set the notification callback.

        Signature: async (job_name: str, result_text: str) -> None
        Called when a cron job produces a result that should be notified.
        NOT called when the result contains [NO_NOTIFY].
        """
        self._notifier = notifier

    def load_persisted_jobs(self) -> int:
        """Load all persisted jobs from the store and register them with APScheduler.

        Returns the number of jobs loaded.
        """
        jobs = self._store.list_jobs()
        count = 0
        for job in jobs:
            if job.enabled:
                self._register_apscheduler_job(job)
                count += 1
        logger.info("Loaded %d persisted cron jobs.", count)
        return count

    def add_job(
        self,
        name: str,
        cron_expression: str,
        action_type: ActionType,
        action_reference: str,
    ) -> CronJob:
        """Create and persist a new cron job."""
        now = datetime.now()
        job = CronJob(
            name=name,
            cron_expression=cron_expression,
            action_type=action_type,
            action_reference=action_reference,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self._store.save_job(job)
        self._register_apscheduler_job(job)
        logger.info("Added cron job: %s (%s)", name, cron_expression)
        return job

    def list_jobs(self) -> list[CronJob]:
        return self._store.list_jobs()

    def get_job(self, name: str) -> CronJob | None:
        return self._store.get_job(name)

    def update_job(self, name: str, **kwargs: object) -> CronJob:
        """Update a cron job's attributes."""
        job = self._store.get_job(name)
        if job is None:
            raise CronJobNotFoundError(f"Cron job '{name}' not found")

        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = datetime.now()

        self._store.save_job(job)

        # Re-register with APScheduler if schedule or enabled changed
        self._unregister_apscheduler_job(name)
        if job.enabled:
            self._register_apscheduler_job(job)

        logger.info("Updated cron job: %s", name)
        return job

    def remove_job(self, name: str) -> bool:
        self._unregister_apscheduler_job(name)
        deleted = self._store.delete_job(name)
        if deleted:
            logger.info("Removed cron job: %s", name)
        return deleted

    def enable_job(self, name: str) -> CronJob:
        return self.update_job(name, enabled=True)

    def disable_job(self, name: str) -> CronJob:
        return self.update_job(name, enabled=False)

    async def execute_job(self, job_name: str) -> CronJobExecution:
        """Execute a cron job's target action and log the result."""
        job = self._store.get_job(job_name)
        if job is None:
            raise CronJobNotFoundError(f"Cron job '{job_name}' not found")

        execution = CronJobExecution(
            job_name=job_name,
            started_at=datetime.now(),
            status=ExecutionStatus.RUNNING,
        )
        exec_id = self._store.log_execution(execution)

        try:
            result = await self._run_action(job)
            self._store.update_execution(
                exec_id,
                finished_at=datetime.now(),
                status=ExecutionStatus.SUCCESS,
                result_summary=result[:500] if result else "",
            )
            execution.status = ExecutionStatus.SUCCESS
            execution.result_summary = result[:500] if result else ""
            logger.info("Cron job '%s' executed successfully.", job_name)

        except Exception as exc:
            error_msg = str(exc)
            self._store.update_execution(
                exec_id,
                finished_at=datetime.now(),
                status=ExecutionStatus.FAILURE,
                error_details=error_msg[:1000],
            )
            execution.status = ExecutionStatus.FAILURE
            execution.error_details = error_msg[:1000]
            logger.error("Cron job '%s' failed: %s", job_name, error_msg)

        execution.finished_at = datetime.now()
        return execution

    async def _run_action(self, job: CronJob) -> str:
        """Execute the job's target action, filter NO_NOTIFY, and notify.

        Flow:
        1. Execute action (prompt or recipe) via process_cron_message
        2. Check for [NO_NOTIFY] in response → skip notification
        3. Otherwise call notifier callback (Telegram, etc.)
        """
        response = await self._execute_action(job)

        # NO_NOTIFY filtering
        if response and _NO_NOTIFY_TOKEN in response:
            logger.info(
                "Cron job '%s': nothing to notify, skipping.", job.name
            )
            return response

        # Send notification
        if response and self._notifier:
            try:
                await self._notifier(job.name, response)
                logger.info(
                    "Cron '%s' notification sent: %d chars",
                    job.name, len(response),
                )
            except Exception:
                logger.exception(
                    "Failed to send notification for cron '%s'", job.name
                )

        return response or "[No output]"

    async def _execute_action(self, job: CronJob) -> str:
        """Execute the job's target action (prompt or recipe).

        Uses process_cron_message (isolated context) when an agent
        orchestrator is available.
        """
        if job.action_type == ActionType.RECIPE:
            from simpleclaw.recipes.loader import load_recipe
            from simpleclaw.recipes.executor import execute_recipe

            recipe = load_recipe(Path(job.action_reference))
            guard = getattr(self._agent, "_command_guard", None) if self._agent else None
            result = await execute_recipe(recipe, command_guard=guard)

            if self._agent and result.success:
                prompt_output = "\n".join(
                    sr.output for sr in (result.step_results or [])
                    if sr.output
                )
                if prompt_output:
                    return await self._agent.process_cron_message(prompt_output)

            succeeded = sum(1 for s in result.step_results if s.success)
            total = len(result.step_results)
            return f"Recipe completed: {succeeded}/{total} steps succeeded"

        elif job.action_type == ActionType.PROMPT:
            if self._agent:
                return await self._agent.process_cron_message(
                    job.action_reference
                )
            return f"Prompt scheduled: {job.action_reference[:200]}"

        return "Unknown action type"

    def _register_apscheduler_job(self, job: CronJob) -> None:
        """Register a job with APScheduler using CronTrigger."""
        try:
            parts = job.cron_expression.split()
            if len(parts) != 5:
                logger.error(
                    "Invalid cron expression for job '%s': %s",
                    job.name,
                    job.cron_expression,
                )
                return

            trigger = CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
            )

            self._apscheduler.add_job(
                self.execute_job,
                trigger=trigger,
                args=[job.name],
                id=f"cron_{job.name}",
                name=f"cron-{job.name}",
                replace_existing=True,
            )
        except Exception:
            logger.exception(
                "Failed to register APScheduler job: %s", job.name
            )

    def _unregister_apscheduler_job(self, name: str) -> None:
        """Remove a job from APScheduler if it exists."""
        job_id = f"cron_{name}"
        try:
            self._apscheduler.remove_job(job_id)
        except Exception:
            pass  # Job may not be registered
