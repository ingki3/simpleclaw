"""스킬 스크립트 실행기: 비동기 서브프로세스를 통해 스킬을 실행한다.

동작 흐름:
1. 스킬의 script_path 유효성을 검증
2. 파일 확장자에 따라 적절한 인터프리터로 실행 명령 구성
3. 비동기 서브프로세스로 실행하고, 타임아웃 및 에러를 처리
4. 스킬에 ``RetryPolicy``가 정의되어 있고 ``idempotent=True``이면 일시적 실패에 대해
   지수 백오프로 자동 재시도

설계 결정:
- 보안을 위해 filter_env()로 환경 변수를 필터링하고, 프로세스 그룹을 분리
- 타임아웃 시 프로세스 그룹 전체를 종료하여 좀비 프로세스를 방지
- 자동 재시도는 멱등성 가드(``RetryPolicy.idempotent``)가 있어야만 활성화 — 외부
  부수효과(메일 전송 등)가 있는 스킬을 사용자 의도 없이 중복 실행하지 않기 위함
- 타임아웃 재시도는 옵트인(``retry_on_timeout``) — 보통 더 깊은 문제를 시사하므로
  기본값은 비활성
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.logging.trace_context import inject_trace_id_env
from simpleclaw.security import filter_env, get_preexec_fn, kill_process_group
from simpleclaw.skills.models import (
    RetryPolicy,
    SkillDefinition,
    SkillError,
    SkillExecutionError,
    SkillNotFoundError,
    SkillResult,
    SkillTimeoutError,
)

if TYPE_CHECKING:
    from simpleclaw.logging.metrics import MetricsCollector

logger = logging.getLogger(__name__)


async def execute_skill(
    skill: SkillDefinition,
    args: list[str] | None = None,
    timeout: int = 60,
    *,
    metrics: MetricsCollector | None = None,
) -> SkillResult:
    """스킬의 대상 스크립트를 비동기 서브프로세스로 실행한다.

    스킬에 ``RetryPolicy``가 정의되어 있고 멱등성 가드가 활성화된 경우, 일시적 실패
    (``SkillExecutionError`` 또는 옵트인 시 ``SkillTimeoutError``)에 대해 지수 백오프로
    자동 재시도한다. ``SkillNotFoundError``처럼 영속적인 오류는 재시도하지 않는다.

    Args:
        skill: 실행할 스킬 정의
        args: 스크립트에 전달할 선택적 인자 목록
        timeout: 최대 실행 시간 (초)
        metrics: 타임아웃 시 ``kill_process_group`` 결과와 재시도 카운터를 누적할
            메트릭 수집기. None이면 기록되지 않으며 기존 동작과 호환된다.

    Returns:
        출력, 종료 코드, 에러 정보, 시도 횟수를 포함하는 SkillResult

    Raises:
        SkillNotFoundError: 스크립트 경로가 없거나 파일이 존재하지 않을 때
        SkillTimeoutError: 실행 시간이 timeout을 초과할 때 (재시도 한도 소진 후)
        SkillExecutionError: 스크립트가 비정상 종료(exit code != 0)할 때 (재시도 한도
            소진 후)
    """
    if not skill.script_path:
        raise SkillNotFoundError(f"Skill '{skill.name}' has no script path defined")

    script = Path(skill.script_path)
    if not script.is_file():
        raise SkillNotFoundError(
            f"Script not found for skill '{skill.name}': {skill.script_path}"
        )

    policy = skill.retry_policy
    # 정책이 없거나 멱등성 가드가 꺼져 있으면 재시도 0회 — 기존 동작과 동일.
    max_retries = policy.max_retries if policy and policy.enabled else 0

    attempt = 0
    last_error: SkillError | None = None
    while True:
        attempt += 1
        try:
            result = await _run_once(skill, script, args, timeout, metrics=metrics)
        except SkillExecutionError as exc:
            last_error = exc
            if not _should_retry(policy, attempt, max_retries, timeout=False):
                _record_exhausted_if_retried(metrics, attempt)
                raise
        except SkillTimeoutError as exc:
            last_error = exc
            if not _should_retry(policy, attempt, max_retries, timeout=True):
                _record_exhausted_if_retried(metrics, attempt)
                raise
        else:
            # 성공 — 재시도가 있었다면 회복으로 기록하고 attempts를 채워 반환.
            if attempt > 1 and metrics is not None:
                metrics.record_skill_retry_recovered()
            result.attempts = attempt
            return result

        # 다음 시도 전 백오프 대기 — _should_retry가 True를 반환했을 때만 도달한다.
        delay = policy.compute_backoff(attempt - 1) if policy else 0.0
        logger.warning(
            "Skill '%s' attempt %d/%d failed (%s); retrying in %.2fs",
            skill.name,
            attempt,
            max_retries + 1,
            type(last_error).__name__,
            delay,
        )
        if metrics is not None:
            metrics.record_skill_retry()
        if delay > 0:
            await asyncio.sleep(delay)


def _should_retry(
    policy: RetryPolicy | None,
    attempt: int,
    max_retries: int,
    *,
    timeout: bool,
) -> bool:
    """현재 시도 결과를 재시도해야 하는지 판단한다.

    Args:
        policy: 스킬의 재시도 정책 (None이면 항상 False).
        attempt: 방금 끝난 시도 번호 (1부터).
        max_retries: 정책에서 허용된 최대 재시도 횟수.
        timeout: 방금 실패가 타임아웃이면 True.

    Returns:
        True이면 호출자가 백오프 후 한 번 더 시도해야 한다.
    """
    if policy is None or not policy.enabled:
        return False
    if attempt > max_retries:
        return False
    if timeout and not policy.retry_on_timeout:
        return False
    return True


def _record_exhausted_if_retried(
    metrics: MetricsCollector | None,
    attempt: int,
) -> None:
    """실제로 재시도를 한 번이라도 수행한 후 실패했을 때만 exhausted 메트릭을 기록한다.

    ``attempt``가 1이라는 것은 첫 시도에서 실패한 뒤 재시도 자격이 없어 (예: 정책
    부재, ``retry_on_timeout=False``) 즉시 전파됐다는 의미다. 이 경우는 회복 시도가
    없었으므로 "회복 실패" 카운터에 잡지 않는다 — 회복력 메트릭의 의미를 보존한다.
    """
    if metrics is None:
        return
    if attempt <= 1:
        return
    metrics.record_skill_retry_exhausted()


async def _run_once(
    skill: SkillDefinition,
    script: Path,
    args: list[str] | None,
    timeout: int,
    *,
    metrics: MetricsCollector | None,
) -> SkillResult:
    """스킬을 한 번 실행한다 (재시도 루프 단위).

    재시도 정책의 한 시도를 캡슐화하기 위해 분리된 헬퍼이다. 호출자(``execute_skill``)
    가 정책을 해석하고 백오프/재시도 결정을 내린다.
    """
    # 파일 확장자에 따라 실행 명령 구성
    cmd = _build_command(script)
    if args:
        cmd.extend(args)

    # filter_env()가 민감 키를 제거한 뒤 trace_id를 주입한다 — 분산 트레이싱
    # 식별자는 비밀이 아니므로 차단 패턴 대상이 아니지만, 명시적으로 마지막에
    # 추가하여 환경변수 차단 패턴 변경에도 영향을 받지 않도록 한다.
    env = inject_trace_id_env(filter_env())

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=skill.skill_dir or None,
            env=env,
            preexec_fn=get_preexec_fn(),
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await kill_process_group(process, metrics=metrics)
        raise SkillTimeoutError(
            f"Skill '{skill.name}' timed out after {timeout}s"
        )
    except FileNotFoundError:
        raise SkillNotFoundError(
            f"Cannot execute script for skill '{skill.name}': {cmd[0]} not found"
        )

    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    exit_code = process.returncode or 0
    success = exit_code == 0

    if not success:
        logger.warning(
            "Skill '%s' exited with code %d: %s",
            skill.name, exit_code, error,
        )
        raise SkillExecutionError(
            f"Skill '{skill.name}' failed (exit {exit_code}): {error}"
        )

    return SkillResult(
        output=output,
        exit_code=exit_code,
        error=error,
        success=True,
    )


def _build_command(script: Path) -> list[str]:
    """파일 확장자에 따라 적절한 실행 명령을 구성한다.

    .py -> python, .sh -> bash, .js -> node, 그 외 -> 직접 실행

    Args:
        script: 실행할 스크립트 파일 경로

    Returns:
        실행 명령 리스트 (예: ["python", "/path/to/script.py"])
    """
    suffix = script.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(script)]
    elif suffix == ".sh":
        return ["bash", str(script)]
    elif suffix == ".js":
        return ["node", str(script)]
    else:
        # 직접 실행 시도 (실행 권한 필요)
        return [str(script)]
