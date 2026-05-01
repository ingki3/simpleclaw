"""레시피 실행기 — v1(스텝 기반) 실행 + v2(instructions) 변수 렌더링.

v1 동작 흐름:
1. 필수 파라미터 검증 및 기본값 적용
2. 각 스텝의 content에 ${변수명} 패턴을 실제 값으로 치환
3. COMMAND 스텝은 셸 명령으로 실행, PROMPT 스텝은 텍스트만 반환
4. 실패 정책(``on_error``)에 따라 abort / continue / rollback 처리

v2 동작 흐름:
1. render_instructions()로 내장 변수(today 등) + 사용자 변수를 치환
2. 치환된 instructions 텍스트를 LLM에 전달 (호출자 책임)

설계 결정:
- 단계별 결과는 ``StepStatus`` (success/skipped/failed) 로 표현하여
  단순 boolean을 넘어선 상세 상태를 보존한다.
- ``on_error`` 정책은 레시피 단위 기본값과 스텝 단위 오버라이드를 모두 지원한다.
- 사용자 노출용 ``error`` 와 디버그용 ``debug_log`` 를 분리하여
  채널/노출 정책을 호출자가 결정할 수 있게 한다.
- ``resume_from`` 으로 실패 지점부터 재실행할 수 있도록 한다(이전 단계는 SKIPPED).
- PROMPT 스텝의 LLM 호출은 호출자의 책임 (관심사 분리)
- CommandGuard를 통한 위험 명령 차단으로 보안 강화
- 내장 변수는 레시피 모듈에서 공통 관리 (Cron/슬래시 명령 모두 동일 적용)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from simpleclaw.recipes.models import (
    OnErrorPolicy,
    RecipeDefinition,
    RecipeExecutionError,
    RecipeResult,
    RecipeStep,
    StepResult,
    StepStatus,
    StepType,
)
from simpleclaw.security import (
    CommandGuard,
    DangerousCommandError,
    filter_env,
    get_preexec_fn,
    kill_process_group,
)

if TYPE_CHECKING:
    from simpleclaw.logging.metrics import MetricsCollector

logger = logging.getLogger(__name__)

# 사용자 노출용 에러 요약 길이 상한.
# stderr 전체를 그대로 보여주면 비밀 경로/스택 정보가 새어나갈 수 있어
# 한 줄 + 200자 정도로 절단한다. 상세 내용은 debug_log 채널로만 전달.
_USER_ERROR_MAX = 200


async def execute_recipe(
    recipe: RecipeDefinition,
    variables: dict[str, str] | None = None,
    timeout: int = 60,
    command_guard: CommandGuard | None = None,
    *,
    metrics: MetricsCollector | None = None,
    resume_from: str | None = None,
) -> RecipeResult:
    """레시피의 스텝들을 순차 실행한다.

    ${name} 구문을 사용하여 스텝 내용에 변수를 치환한다.
    실패 시 동작은 레시피/스텝의 ``on_error`` 정책을 따른다.

    Args:
        recipe: 실행할 레시피 정의
        variables: 스텝 내용에 치환할 변수 딕셔너리
        timeout: 각 명령의 최대 실행 시간 (초)
        command_guard: 위험 명령 차단을 위한 가드 (None이면 검사 생략)
        metrics: 타임아웃 시 ``kill_process_group`` 결과를 누적할 메트릭 수집기.
        resume_from: 지정 시 해당 이름의 스텝부터 실행하고, 그 이전 스텝은
            SKIPPED 로 기록한다. 이전 실패의 재실행(resume) 용도.

    Returns:
        전체 실행 결과를 담은 RecipeResult
    """
    variables = variables or {}

    # 필수 파라미터 검증
    for param in recipe.parameters:
        if param.required and param.name not in variables:
            if param.default:
                variables[param.name] = param.default
            else:
                raise RecipeExecutionError(
                    f"Required parameter '{param.name}' not provided "
                    f"for recipe '{recipe.name}'"
                )

    # 선택적 파라미터에 기본값 적용
    for param in recipe.parameters:
        if param.name not in variables and param.default:
            variables[param.name] = param.default

    if not recipe.steps:
        logger.warning("Recipe '%s' has no steps.", recipe.name)
        return RecipeResult(recipe_name=recipe.name, success=True)

    # resume_from이 실제 스텝에 존재하는지 미리 검증해 사용자에게 명확한 오류를 준다.
    if resume_from is not None and not any(
        s.name == resume_from for s in recipe.steps
    ):
        raise RecipeExecutionError(
            f"resume_from='{resume_from}' not found in recipe '{recipe.name}'"
        )

    default_policy = recipe.on_error or OnErrorPolicy.ABORT

    step_results: list[StepResult] = []
    rollback_results: list[StepResult] = []
    failed_steps: list[str] = []
    debug_chunks: list[str] = []
    error_chunks: list[str] = []

    started = resume_from is None

    for idx, step in enumerate(recipe.steps):
        # resume_from 이전 스텝은 실행하지 않고 SKIPPED 로 기록
        if not started:
            if step.name == resume_from:
                started = True
            else:
                step_results.append(
                    StepResult(step_name=step.name, status=StepStatus.SKIPPED)
                )
                continue

        result = await _run_step(
            step,
            variables,
            timeout,
            command_guard=command_guard,
            metrics=metrics,
        )
        step_results.append(result)

        if result.debug_log:
            debug_chunks.append(f"[{step.name}] {result.debug_log}")

        if result.failed:
            failed_steps.append(step.name)
            error_chunks.append(f"[{step.name}] {result.error}")

            step_policy = step.on_error or default_policy

            if step_policy == OnErrorPolicy.CONTINUE:
                # 다음 스텝을 계속 실행. 실패는 누적되어 최종 결과에 반영.
                continue

            # ABORT / ROLLBACK 공통: 이후 스텝을 모두 SKIPPED로 기록하고 종료
            for remaining in recipe.steps[idx + 1 :]:
                step_results.append(
                    StepResult(
                        step_name=remaining.name, status=StepStatus.SKIPPED
                    )
                )

            if step_policy == OnErrorPolicy.ROLLBACK:
                rollback_results = await _run_rollbacks(
                    recipe.steps[:idx],
                    step_results[:idx],
                    variables,
                    timeout,
                    command_guard=command_guard,
                    metrics=metrics,
                )

            break

    overall_success = not failed_steps
    first_failed = failed_steps[0] if failed_steps else ""

    error_summary = "\n".join(error_chunks)
    debug_log = "\n".join(c for c in debug_chunks if c)

    return RecipeResult(
        recipe_name=recipe.name,
        success=overall_success,
        step_results=step_results,
        failed_step=first_failed,
        failed_steps=failed_steps,
        # 호환 필드: 기존 호출자(`result.error`)가 첫 실패 메시지를 기대한다.
        error=error_chunks[0].split("] ", 1)[-1] if error_chunks else "",
        error_summary=error_summary,
        debug_log=debug_log,
        # 가장 빨리 실패한 스텝부터 resume 가능. CONTINUE 정책으로 여러 스텝이
        # 실패한 경우라도 첫 실패 지점부터 재실행하는 것이 직관적이다.
        resumable_from=first_failed,
        rollback_results=rollback_results,
    )


async def _run_step(
    step: RecipeStep,
    variables: dict[str, str],
    timeout: int,
    command_guard: CommandGuard | None,
    metrics: MetricsCollector | None,
) -> StepResult:
    """단일 스텝을 실행하여 ``StepResult`` 를 반환한다.

    PROMPT 스텝은 변수 치환된 텍스트만 SUCCESS 결과로 돌려주고,
    실제 LLM 호출은 호출자의 책임이다.
    """
    content = _substitute_variables(step.content, variables)

    if step.step_type == StepType.COMMAND:
        return await _execute_command(
            step.name,
            content,
            timeout,
            command_guard=command_guard,
            metrics=metrics,
        )

    if step.step_type == StepType.PROMPT:
        return StepResult(
            step_name=step.name,
            status=StepStatus.SUCCESS,
            output=content,
        )

    # 알 수 없는 타입 — 정의 단계에서 막혀야 정상이지만 방어적으로 FAILED 처리.
    return StepResult(
        step_name=step.name,
        status=StepStatus.FAILED,
        error=f"Unknown step type: {step.step_type}",
        debug_log=f"Unknown step type: {step.step_type!r}",
    )


async def _run_rollbacks(
    completed_steps: list[RecipeStep],
    completed_results: list[StepResult],
    variables: dict[str, str],
    timeout: int,
    command_guard: CommandGuard | None,
    metrics: MetricsCollector | None,
) -> list[StepResult]:
    """롤백 정책 발동 시, 성공한 스텝들의 ``rollback`` 명령을 역순 실행한다.

    - 이미 SKIPPED/FAILED인 스텝은 대상에서 제외 (실제로 일어나지 않은 부수효과는
      되돌릴 필요가 없다).
    - 롤백 명령이 비어있는 스텝은 건너뛴다.
    - 개별 롤백 명령의 실패는 다른 롤백을 막지 않는다(베스트 에포트).
    """
    rollback_outputs: list[StepResult] = []
    pairs = list(zip(completed_steps, completed_results))
    for step, result in reversed(pairs):
        if result.status != StepStatus.SUCCESS:
            continue
        if not step.rollback:
            continue

        rollback_cmd = _substitute_variables(step.rollback, variables)
        rb_result = await _execute_command(
            f"rollback:{step.name}",
            rollback_cmd,
            timeout,
            command_guard=command_guard,
            metrics=metrics,
        )
        rollback_outputs.append(rb_result)

    return rollback_outputs


def render_instructions(
    instructions: str,
    variables: dict[str, str] | None = None,
) -> str:
    """v2 레시피의 instructions에 내장 변수와 사용자 변수를 치환한다.

    내장 변수 (실행 시점 KST 기준 자동 주입):
      {{ today }}    — 2026-04-27
      {{ today_ko }} — 2026년 04월 27일
      {{ weekday }}  — Sunday
      {{ now }}      — 2026-04-27 07:15

    사용자 변수:
      {{ variable }} — parameters에 정의된 값

    Args:
        instructions: 원본 instructions 텍스트
        variables: 사용자 정의 변수 딕셔너리 (슬래시 명령어에서 전달)

    Returns:
        변수 치환이 완료된 instructions 텍스트
    """
    from datetime import datetime, timezone, timedelta

    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)

    # 내장 변수
    all_vars = {
        "today": now.strftime("%Y-%m-%d"),
        "today_ko": now.strftime("%Y년 %m월 %d일"),
        "weekday": now.strftime("%A"),
        "now": now.strftime("%Y-%m-%d %H:%M"),
    }
    # 사용자 변수로 덮어쓰기 (동명 시 사용자 변수 우선)
    if variables:
        all_vars.update(variables)

    result = instructions
    for key, val in all_vars.items():
        result = result.replace("{{ " + key + " }}", val)
        result = result.replace("{{" + key + "}}", val)
    return result


def _substitute_variables(content: str, variables: dict[str, str]) -> str:
    """${variable_name} 패턴을 실제 값으로 치환한다.

    매칭되지 않는 변수는 원본 패턴을 그대로 유지한다.
    """
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    return re.sub(r"\$\{(\w+)\}", replacer, content)


def _summarize_error(stderr: str, fallback: str) -> str:
    """stderr를 사용자 노출용 한 줄 요약으로 줄인다.

    상세 stderr는 별도로 ``debug_log`` 에 보존되므로,
    여기서는 첫 줄 + 길이 제한만 적용한다.
    """
    text = (stderr or "").strip()
    if not text:
        return fallback
    first_line = text.splitlines()[0].strip()
    if len(first_line) > _USER_ERROR_MAX:
        return first_line[: _USER_ERROR_MAX - 1] + "…"
    return first_line


async def _execute_command(
    name: str,
    command: str,
    timeout: int,
    command_guard: CommandGuard | None = None,
    *,
    metrics: MetricsCollector | None = None,
) -> StepResult:
    """셸 명령을 실행하고 결과를 반환한다.

    Args:
        name: 스텝 이름 (결과 추적용)
        command: 실행할 셸 명령 문자열
        timeout: 최대 실행 시간 (초)
        command_guard: 위험 명령 차단 가드

    Returns:
        실행 결과를 담은 StepResult.

        실패 시 ``error`` 는 사용자 노출용 한 줄 요약을, ``debug_log`` 는
        stdout/stderr 상세 내용을 담는다.
    """
    # 보안: 위험 명령 패턴 검사
    if command_guard is not None:
        try:
            command_guard.check(command)
        except DangerousCommandError as exc:
            return StepResult(
                step_name=name,
                status=StepStatus.FAILED,
                error=f"Command blocked: {exc.description}",
                debug_log=f"DangerousCommandError: {exc.description}",
            )

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=filter_env(),
            preexec_fn=get_preexec_fn(),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        await kill_process_group(process, metrics=metrics)
        return StepResult(
            step_name=name,
            status=StepStatus.FAILED,
            error=f"Command timed out after {timeout}s",
            debug_log=f"Timeout after {timeout}s for command: {command}",
        )

    output = stdout.decode("utf-8", errors="replace").strip()
    error_text = stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        user_error = (
            f"Exit code {process.returncode}: "
            f"{_summarize_error(error_text, 'command failed')}"
        )
        debug_log = (
            f"exit={process.returncode}\n"
            f"--- stderr ---\n{error_text}\n"
            f"--- stdout ---\n{output}"
        )
        return StepResult(
            step_name=name,
            status=StepStatus.FAILED,
            output=output,
            error=user_error,
            debug_log=debug_log,
        )

    return StepResult(
        step_name=name,
        status=StepStatus.SUCCESS,
        output=output,
    )
