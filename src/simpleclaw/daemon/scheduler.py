"""크론 스케줄러: APScheduler 래퍼 + 크론 작업 CRUD 관리.

크론 작업의 생성·수정·삭제·실행을 담당하며, APScheduler와 SQLite 영속화를 통합한다.

주요 기능:
- 크론 작업 CRUD: 이름 기반으로 작업을 관리하고 APScheduler에 등록/해제
- NO_NOTIFY 필터링: LLM 응답에 [NO_NOTIFY] 토큰이 포함되면 알림 건너뜀
- 플러그형 알림 콜백: 크론 결과를 외부 채널(텔레그램 등)로 전달
- process_cron_message 격리: 크론 작업은 대화 히스토리와 분리된 컨텍스트에서 실행
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
    """APScheduler 통합과 SQLite 영속화를 통해 크론 작업을 관리한다.

    주요 기능:
    - NO_NOTIFY 필터링: LLM 응답에 [NO_NOTIFY]가 포함되면 알림 건너뜀
    - 플러그형 알림 콜백: 크론 결과를 외부 채널(텔레그램 등)로 전달
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
        """알림 콜백을 설정한다.

        시그니처: async (job_name: str, result_text: str) -> None
        크론 작업이 알림 대상 결과를 생성했을 때 호출된다.
        응답에 [NO_NOTIFY]가 포함되면 호출되지 않는다.
        """
        self._notifier = notifier

    def load_persisted_jobs(self) -> int:
        """DB에 저장된 모든 작업을 APScheduler에 등록한다.

        Returns:
            로드된 활성 작업 수
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
        """새 크론 작업을 생성하고 DB 및 APScheduler에 등록한다."""
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
        """모든 크론 작업을 반환한다."""
        return self._store.list_jobs()

    def get_job(self, name: str) -> CronJob | None:
        """이름으로 크론 작업을 조회한다."""
        return self._store.get_job(name)

    def update_job(self, name: str, **kwargs: object) -> CronJob:
        """크론 작업의 속성을 업데이트한다. 스케줄 변경 시 APScheduler 재등록."""
        job = self._store.get_job(name)
        if job is None:
            raise CronJobNotFoundError(f"Cron job '{name}' not found")

        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)
        job.updated_at = datetime.now()

        self._store.save_job(job)

        # 스케줄이나 활성 상태 변경 시 APScheduler에 재등록
        self._unregister_apscheduler_job(name)
        if job.enabled:
            self._register_apscheduler_job(job)

        logger.info("Updated cron job: %s", name)
        return job

    def remove_job(self, name: str) -> bool:
        """크론 작업을 APScheduler와 DB에서 모두 삭제한다."""
        self._unregister_apscheduler_job(name)
        deleted = self._store.delete_job(name)
        if deleted:
            logger.info("Removed cron job: %s", name)
        return deleted

    def enable_job(self, name: str) -> CronJob:
        """크론 작업을 활성화한다."""
        return self.update_job(name, enabled=True)

    def disable_job(self, name: str) -> CronJob:
        """크론 작업을 비활성화한다."""
        return self.update_job(name, enabled=False)

    async def execute_job(self, job_name: str) -> CronJobExecution:
        """크론 작업의 대상 액션을 실행하고 결과를 DB에 기록한다."""
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
        """작업의 대상 액션을 실행하고, NO_NOTIFY 필터링 후 알림을 전송한다.

        처리 흐름:
        1. process_cron_message를 통해 액션(프롬프트/레시피) 실행
        2. 응답에 [NO_NOTIFY]가 포함되면 알림 건너뜀
        3. 그 외에는 알림 콜백(텔레그램 등) 호출
        """
        response = await self._execute_action(job)

        # NO_NOTIFY 토큰 감지 시 알림 건너뜀
        if response and _NO_NOTIFY_TOKEN in response:
            logger.info(
                "Cron job '%s': nothing to notify, skipping.", job.name
            )
            return response

        # 알림 콜백을 통해 외부 채널로 결과 전송
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
        """작업의 대상 액션(프롬프트 또는 레시피)을 실행한다.

        에이전트 오케스트레이터가 있으면 process_cron_message를 통해
        대화 히스토리와 격리된 컨텍스트에서 실행한다.
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
        """CronTrigger를 사용하여 APScheduler에 작업을 등록한다."""
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
        """APScheduler에서 작업을 제거한다 (미등록이면 무시)."""
        job_id = f"cron_{name}"
        try:
            self._apscheduler.remove_job(job_id)
        except Exception:
            pass  # 미등록 작업이면 예외 무시
