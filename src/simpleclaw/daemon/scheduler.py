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
from simpleclaw.recipes.models import StepType
from simpleclaw.proactive.event_detector import EventDetector

logger = logging.getLogger(__name__)

_NO_NOTIFY_TOKEN = "[NO_NOTIFY]"

# 재시도 사이 백오프를 실제로 sleep하는 함수 (테스트에서 monkeypatch로 0초화).
_sleep = asyncio.sleep


def _translate_cron_dow_to_apscheduler(field: str) -> str:
    """표준 crontab의 day_of_week 표기를 APScheduler 표기로 변환한다.

    표준 cron: 0/7=sun, 1=mon, ..., 6=sat (예: "1-5" = mon-fri).
    APScheduler: 0=mon, 1=tue, ..., 6=sun (즉 "1-5" = tue-sat).

    Why: APScheduler가 숫자 day_of_week를 그대로 받으면 사용자가 표준 cron 의미로
    입력한 "1-5"가 화-토로 잘못 해석돼 월요일이 누락되는 사고가 발생했다(2026-05-18,
    `krstock-auto` 잡 미실행). 등록 시점에 숫자 토큰만 안전하게 매핑해서 사용자 입력을
    DB에 원형 그대로 보존하면서 트리거만 올바르게 등록한다.

    처리 대상:
        - 콤마 리스트 (`1,3,5`)
        - 범위 (`1-5`) — 매핑 후 정렬된 콤마 리스트로 풀어 wrap-around 회피
        - 단일 숫자 (`3`)
        - `*`, 영문 약어(`mon`, `mon-fri`), 스텝 표현(`*/2`, `1-5/2`)은 그대로 통과
          (APScheduler가 직접 해석 가능하거나 의미가 모호하므로 변환하지 않음).
    """
    if not field or field == "*":
        return field

    def _convert_token(token: str) -> str:
        token = token.strip()
        if "/" in token or not token:
            return token  # 스텝/빈 토큰: 매핑이 모호하므로 그대로 통과
        if "-" in token:
            a_s, b_s = token.split("-", 1)
            if not (a_s.isdigit() and b_s.isdigit()):
                return token  # 영문 약어(mon-fri 등)는 APScheduler가 처리
            a, b = int(a_s), int(b_s)
            if not (0 <= a <= 7 and 0 <= b <= 7):
                return token
            days = range(a, b + 1) if a <= b else list(range(a, 8)) + list(range(0, b + 1))
            mapped = sorted({(d - 1) % 7 for d in days})
            return ",".join(str(m) for m in mapped)
        if token.isdigit():
            n = int(token)
            if 0 <= n <= 7:
                return str((n - 1) % 7)
        return token  # 알 수 없는 형식은 보존

    converted = [_convert_token(t) for t in field.split(",")]

    # 모든 토큰이 순수 숫자/숫자리스트로 변환되면 정렬·중복 제거해 결정적 출력을 만든다.
    # (영문 약어/스텝/* 등 비숫자 토큰이 섞이면 원본 순서를 보존.)
    expanded: list[int] = []
    only_numeric = True
    for tok in converted:
        for sub in tok.split(","):
            if sub.isdigit():
                expanded.append(int(sub))
            else:
                only_numeric = False
                break
        if not only_numeric:
            break
    if only_numeric and expanded:
        return ",".join(str(n) for n in sorted(set(expanded)))
    return ",".join(converted)


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
        event_detector: EventDetector | None = None,
        recipes_dir: str | Path = "~/.simpleclaw-agent/default/recipes",
        legacy_recipes_dir: str | Path | None = ".agent/recipes",
    ) -> None:
        self._store = store
        self._apscheduler = apscheduler
        self._recipe_executor = recipe_executor
        self._agent = agent_orchestrator
        self._notifier = notifier
        self._event_detector = event_detector
        # BIZ-202: cron 의 action_reference 가 이름(예: "krstock") 일 때 어느 디렉터리에서
        # `recipe.yaml` 을 찾을지. 봇/데몬이 동일 절대 경로(`~/.simpleclaw-agent/default/recipes`)를 보도록
        # 호출 측(run_bot.py)에서 명시적으로 주입. 레거시 `.agent/recipes/` 는 한 번 폴백.
        self._recipes_dir = Path(recipes_dir).expanduser()
        self._legacy_recipes_dir = (
            Path(legacy_recipes_dir).expanduser() if legacy_recipes_dir else None
        )

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
            await self._capture_cron_event(
                event_type="circuit_break",
                job=job,
                error_details=last_error,
                attempt=max(1, int(job.max_attempts)),
                max_attempts=max(1, int(job.max_attempts)),
            )
        else:
            self._store.save_job(job)
            logger.error(
                "Cron job '%s' failed after %d retries (consecutive failures: %d).",
                job.name, max(1, int(job.max_attempts)) - 1,
                job.consecutive_failures,
            )
            await self._capture_cron_event(
                event_type="failure",
                job=job,
                error_details=last_error,
                attempt=max(1, int(job.max_attempts)),
                max_attempts=max(1, int(job.max_attempts)),
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

    async def _capture_cron_event(
        self,
        *,
        event_type: str,
        job: CronJob,
        error_details: str | None = None,
        result_summary: str | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        """cron 이벤트 hook을 best-effort로 실행해 scheduler 본 흐름을 보호한다."""
        if self._event_detector is None:
            return
        try:
            self._event_detector.capture_cron_event(
                event_type=event_type,
                job_name=job.name,
                error_details=error_details,
                result_summary=result_summary,
                attempt=attempt,
                max_attempts=max_attempts,
                payload={
                    "action_type": job.action_type.value,
                    "action_reference": job.action_reference,
                    "consecutive_failures": job.consecutive_failures,
                    "enabled": job.enabled,
                },
            )
        except Exception:  # noqa: BLE001 — proactive hook 실패가 cron 실행/기록을 깨면 안 된다.
            logger.exception("Cron proactive event hook failed for '%s'", job.name)

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

        v1 레시피(steps 기반) 처리 규약 — BIZ-243:
        - 각 스텝의 SUCCESS 출력(COMMAND 의 stdout, PROMPT 의 변수 치환된 content)
          을 줄바꿈으로 합쳐 단일 LLM 호출(`process_cron_message`) 입력으로 사용.
        - 합친 입력이 비어 있으면 LLM 을 호출하지 않고 "Recipe completed" 통지로
          폴백한다. 이는 부수효과만 있는 COMMAND-only 레시피를 위한 의도된 경로.
        - PROMPT 스텝이 있는데 입력이 비는 경우는 로더가 빈 content 를 거부하므로
          정상적으로는 발생할 수 없다. 그래도 안전망으로 WARN 을 남긴다(silent
          no-op 재발 방지).
        """
        if job.action_type == ActionType.RECIPE:
            from simpleclaw.recipes.loader import load_recipe
            from simpleclaw.recipes.executor import execute_recipe

            # action_reference가 파일 경로 또는 레시피 이름일 수 있음
            ref_path = Path(job.action_reference)
            if not ref_path.is_file():
                # BIZ-202: 레시피 이름으로 해석. 봇 채팅에서 만들어 둔 레시피와
                # 데몬이 같은 절대 경로(``~/.simpleclaw-agent/default/recipes/``) 를 보도록 한다.
                # primary 에 없으면 레거시(``.agent/recipes/``) 로 한 번 폴백 + 경고.
                primary = self._recipes_dir / job.action_reference / "recipe.yaml"
                if primary.is_file():
                    ref_path = primary
                elif self._legacy_recipes_dir is not None:
                    legacy = (
                        self._legacy_recipes_dir
                        / job.action_reference
                        / "recipe.yaml"
                    )
                    if legacy.is_file():
                        logger.warning(
                            "Cron '%s' loaded recipe from DEPRECATED path '%s' — "
                            "move it to '%s'. The legacy fallback is scheduled for "
                            "removal in the next minor release (BIZ-202).",
                            job.name, legacy, primary,
                        )
                        ref_path = legacy
                    else:
                        ref_path = primary  # 호출자에게 primary 경로로 명확한 에러 메시지를 노출
                else:
                    ref_path = primary
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

                # BIZ-243 — PROMPT 스텝이 있는데 LLM 으로 보낼 입력이 비는
                # 상태는 로더 검증 이후로는 정상 경로에서 발생하지 않아야 한다.
                # 그래도 silent no-op 재발 시 운영자가 즉시 인지할 수 있도록 WARN.
                if any(
                    s.step_type == StepType.PROMPT for s in recipe.steps
                ):
                    logger.warning(
                        "Cron '%s' recipe '%s' has PROMPT step(s) but produced "
                        "no LLM input — LLM call skipped, falling back to "
                        "'Recipe completed' notification. Check the recipe "
                        "definition for empty PROMPT content.",
                        job.name, recipe.name,
                    )

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
                # 표준 cron(0/7=sun, 1=mon...) ↔ APScheduler(0=mon...6=sun) 매핑 차이를 보정.
                day_of_week=_translate_cron_dow_to_apscheduler(parts[4]),
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
