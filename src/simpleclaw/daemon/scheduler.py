"""크론 스케줄러: APScheduler 래퍼 + 크론 작업 CRUD 관리.

크론 작업의 생성·수정·삭제·실행을 담당하며, APScheduler와 SQLite 영속화를 통합한다.

주요 기능:
- 크론 작업 CRUD: 이름 기반으로 작업을 관리하고 APScheduler에 등록/해제
- NO_NOTIFY 필터링: LLM 응답에 [NO_NOTIFY] 토큰이 포함되면 알림 건너뜀
- 플러그형 알림 콜백: 크론 결과를 외부 채널(텔레그램 등)로 전달
- process_cron_message 격리: 크론 작업은 대화 히스토리와 분리된 컨텍스트에서 실행
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from simpleclaw.daemon.models import (
    ActionType,
    BackoffStrategy,
    CronJob,
    CronJobExecution,
    CronJobNotFoundError,
    ExecutionStatus,
)
from simpleclaw.daemon.store import DaemonStore

logger = logging.getLogger(__name__)

_NO_NOTIFY_TOKEN = "[NO_NOTIFY]"

# 재시도 사이 백오프를 실제로 sleep하는 함수 (테스트에서 monkeypatch로 0초화).
_sleep = asyncio.sleep


def _compute_backoff(
    base_seconds: float, attempt: int, strategy: BackoffStrategy
) -> float:
    """재시도 백오프 시간을 계산한다.

    Args:
        base_seconds: 첫 백오프 간격(초).
        attempt: 방금 실패한 시도 번호(1부터 시작).
        strategy: linear 또는 exponential.

    Returns:
        다음 시도 전 대기 시간(초).
    """
    if base_seconds <= 0 or attempt <= 0:
        return 0.0
    if strategy == BackoffStrategy.LINEAR:
        return base_seconds * attempt
    # 지수형: 60s → 120s → 240s ...
    return base_seconds * (2 ** (attempt - 1))


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
        *,
        max_attempts: int | None = None,
        backoff_seconds: float | None = None,
        backoff_strategy: BackoffStrategy | str | None = None,
        circuit_break_threshold: int | None = None,
    ) -> CronJob:
        """새 크론 작업을 생성하고 DB 및 APScheduler에 등록한다.

        재시도 정책 인자는 모두 선택적이며, 미지정 시 ``CronJob`` dataclass의
        기본값(3회/60s/exponential/threshold=5)을 사용한다.
        """
        now = datetime.now()
        # 재시도 파라미터를 명시적으로 전달받았을 때만 dataclass 기본값을 덮어쓴다.
        kwargs: dict[str, object] = {}
        if max_attempts is not None:
            kwargs["max_attempts"] = max_attempts
        if backoff_seconds is not None:
            kwargs["backoff_seconds"] = backoff_seconds
        if backoff_strategy is not None:
            kwargs["backoff_strategy"] = (
                backoff_strategy
                if isinstance(backoff_strategy, BackoffStrategy)
                else BackoffStrategy(backoff_strategy)
            )
        if circuit_break_threshold is not None:
            kwargs["circuit_break_threshold"] = circuit_break_threshold

        job = CronJob(
            name=name,
            cron_expression=cron_expression,
            action_type=action_type,
            action_reference=action_reference,
            enabled=True,
            created_at=now,
            updated_at=now,
            **kwargs,
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
        """크론 작업을 활성화한다.

        circuit-break로 자동 비활성된 작업을 사용자가 재활성화할 때 누적된
        실패 카운터도 함께 리셋한다(그렇지 않으면 다음 1회 실패만으로
        다시 차단된다).
        """
        return self.update_job(name, enabled=True, consecutive_failures=0)

    def disable_job(self, name: str) -> CronJob:
        """크론 작업을 비활성화한다."""
        return self.update_job(name, enabled=False)

    async def execute_job(self, job_name: str) -> CronJobExecution:
        """크론 작업의 대상 액션을 실행하고 결과를 DB에 기록한다.

        BIZ-19 — 작업 단위 재시도 + 자동 일시 정지(circuit-break):
        1. ``max_attempts`` 만큼 액션을 재시도하며, 시도마다 별도의
           ``cron_executions`` 레코드를 남긴다(``attempt`` 컬럼).
        2. 성공 시 ``consecutive_failures``를 0으로 리셋하고 마지막 성공
           실행을 반환한다.
        3. 모든 시도가 실패하면 ``consecutive_failures`` += 1.
           ``circuit_break_threshold`` 이상이면 작업을 자동 비활성화하고
           알림 콜백으로 통지한다.
        4. LLM API 자체의 재시도와는 책임을 분리한다 — 여기서는 작업의
           실행 결과(예외 발생 여부)만을 기준으로 한다.

        Returns:
            마지막 시도(성공 시 그 시도, 모두 실패 시 마지막 실패 시도)의
            ``CronJobExecution``.
        """
        job = self._store.get_job(job_name)
        if job is None:
            raise CronJobNotFoundError(f"Cron job '{job_name}' not found")

        max_attempts = max(1, int(job.max_attempts))
        last_execution: CronJobExecution | None = None
        last_error: str | None = None

        for attempt in range(1, max_attempts + 1):
            started = datetime.now()
            execution = CronJobExecution(
                job_name=job_name,
                started_at=started,
                status=ExecutionStatus.RUNNING,
                attempt=attempt,
            )
            exec_id = self._store.log_execution(execution)

            try:
                result = await self._run_action(job)
            except Exception as exc:
                error_msg = str(exc)
                finished = datetime.now()
                self._store.update_execution(
                    exec_id,
                    finished_at=finished,
                    status=ExecutionStatus.FAILURE,
                    error_details=error_msg[:1000],
                )
                execution.id = exec_id
                execution.finished_at = finished
                execution.status = ExecutionStatus.FAILURE
                execution.error_details = error_msg[:1000]
                last_execution = execution
                last_error = error_msg
                logger.warning(
                    "Cron job '%s' attempt %d/%d failed: %s",
                    job_name, attempt, max_attempts, error_msg,
                )

                # 마지막 시도가 아니면 백오프 후 재시도.
                if attempt < max_attempts:
                    delay = _compute_backoff(
                        job.backoff_seconds, attempt, job.backoff_strategy
                    )
                    if delay > 0:
                        logger.info(
                            "Cron job '%s' backing off %.1fs before retry %d.",
                            job_name, delay, attempt + 1,
                        )
                        await _sleep(delay)
                continue

            # 성공 — 카운터 리셋 후 즉시 반환.
            finished = datetime.now()
            summary = result[:500] if result else ""
            self._store.update_execution(
                exec_id,
                finished_at=finished,
                status=ExecutionStatus.SUCCESS,
                result_summary=summary,
            )
            execution.id = exec_id
            execution.finished_at = finished
            execution.status = ExecutionStatus.SUCCESS
            execution.result_summary = summary
            self._reset_consecutive_failures(job)
            if attempt > 1:
                logger.info(
                    "Cron job '%s' succeeded on attempt %d/%d.",
                    job_name, attempt, max_attempts,
                )
            else:
                logger.info("Cron job '%s' executed successfully.", job_name)
            return execution

        # 모든 시도 실패: circuit-break 평가.
        await self._record_failure_and_maybe_break(job, last_error or "")
        assert last_execution is not None  # 루프가 1회 이상 돌았음을 보장
        return last_execution

    def _reset_consecutive_failures(self, job: CronJob) -> None:
        """성공 시 누적 실패 카운터를 0으로 리셋한다 (이미 0이면 no-op)."""
        if job.consecutive_failures == 0:
            return
        job.consecutive_failures = 0
        job.updated_at = datetime.now()
        self._store.save_job(job)

    async def _record_failure_and_maybe_break(
        self, job: CronJob, last_error: str
    ) -> None:
        """모든 재시도 실패 후 누적 카운터를 증가시키고 임계값 도달 시 차단한다.

        ``circuit_break_threshold`` <= 0 이면 차단 기능 비활성.
        차단 시 작업을 disabled로 전환하고, 알림 콜백이 있으면 통지를 보낸다.
        """
        job.consecutive_failures = (job.consecutive_failures or 0) + 1
        job.updated_at = datetime.now()
        threshold = int(job.circuit_break_threshold or 0)

        if threshold > 0 and job.consecutive_failures >= threshold:
            # 자동 일시 정지: APScheduler에서 해제하고 enabled=False 영속화.
            job.enabled = False
            self._store.save_job(job)
            self._unregister_apscheduler_job(job.name)
            logger.error(
                "Cron job '%s' circuit-broken after %d consecutive failures; "
                "auto-disabled. Last error: %s",
                job.name, job.consecutive_failures, last_error,
            )
            await self._send_circuit_break_notification(
                job, last_error
            )
        else:
            self._store.save_job(job)
            logger.error(
                "Cron job '%s' failed after %d retries (consecutive failures: %d).",
                job.name, max(1, int(job.max_attempts)) - 1,
                job.consecutive_failures,
            )

    async def _send_circuit_break_notification(
        self, job: CronJob, last_error: str
    ) -> None:
        """circuit-break 발동 시 알림 콜백을 호출한다.

        알림 콜백은 NO_NOTIFY 토큰을 적용하지 않는다 — 차단은 사용자가
        반드시 인지해야 하는 운영 이벤트이기 때문이다.
        """
        if not self._notifier:
            return
        message = (
            f"⚠️ Cron job '{job.name}' auto-disabled after "
            f"{job.consecutive_failures} consecutive failures.\n"
            f"Last error: {last_error[:300]}"
        )
        try:
            await self._notifier(job.name, message)
        except Exception:
            logger.exception(
                "Failed to send circuit-break notification for cron '%s'",
                job.name,
            )

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

            # action_reference가 파일 경로 또는 레시피 이름일 수 있음
            ref_path = Path(job.action_reference)
            if not ref_path.is_file():
                # 레시피 이름으로 해석: .agent/recipes/<name>/recipe.yaml
                ref_path = Path(f".agent/recipes/{job.action_reference}/recipe.yaml")
            recipe = load_recipe(ref_path)

            # v2 레시피 (instructions 필드 사용): 내장 변수 치환 후 LLM에 직접 전달
            if recipe.instructions and self._agent:
                from simpleclaw.recipes.executor import render_instructions
                instructions = render_instructions(recipe.instructions)
                return await self._agent.process_cron_message(instructions)

            # v1 레시피 (steps 기반): 스텝별 실행
            guard = getattr(self._agent, "_command_guard", None) if self._agent else None
            result = await execute_recipe(recipe, command_guard=guard)

            # 디버그 로그는 사용자 채널이 아닌 운영 로그로만 노출한다.
            if result.debug_log:
                logger.debug(
                    "Recipe '%s' debug log:\n%s",
                    recipe.name, result.debug_log,
                )

            if self._agent and result.success:
                prompt_output = "\n".join(
                    sr.output for sr in (result.step_results or [])
                    if sr.output and sr.success
                )
                if prompt_output:
                    return await self._agent.process_cron_message(prompt_output)

            succeeded = sum(1 for s in result.step_results if s.success)
            total = len(result.step_results)
            base = f"Recipe completed: {succeeded}/{total} steps succeeded"
            if result.error_summary:
                # 사용자 노출용 요약만 덧붙인다 (debug_log는 위에서 logger로만 처리).
                base += f"\n{result.error_summary}"
                if result.resumable_from:
                    base += f"\n(resume with: resume_from='{result.resumable_from}')"
            return base

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
