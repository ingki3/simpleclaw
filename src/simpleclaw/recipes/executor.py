"""레시피 실행기 — v1(스텝 기반) 실행 + v2(instructions) 변수 렌더링.

v1 동작 흐름:
1. 필수 파라미터 검증 및 기본값 적용
2. 각 스텝의 content에 ${변수명} 패턴을 실제 값으로 치환
3. COMMAND 스텝은 셸 명령으로 실행, PROMPT 스텝은 텍스트만 반환
4. 첫 번째 실패 스텝에서 실행 중단

v2 동작 흐름:
1. render_instructions()로 내장 변수(today 등) + 사용자 변수를 치환
2. 치환된 instructions 텍스트를 LLM에 전달 (호출자 책임)

설계 결정:
- PROMPT 스텝의 LLM 호출은 호출자의 책임 (관심사 분리)
- CommandGuard를 통한 위험 명령 차단으로 보안 강화
- 내장 변수는 레시피 모듈에서 공통 관리 (Cron/슬래시 명령 모두 동일 적용)
"""

from __future__ import annotations

import asyncio
import logging
import re

from simpleclaw.recipes.models import (
    RecipeDefinition,
    RecipeExecutionError,
    RecipeResult,
    StepResult,
    StepType,
)
from simpleclaw.security import (
    CommandGuard,
    DangerousCommandError,
    filter_env,
    get_preexec_fn,
    kill_process_group,
)

logger = logging.getLogger(__name__)


async def execute_recipe(
    recipe: RecipeDefinition,
    variables: dict[str, str] | None = None,
    timeout: int = 60,
    command_guard: CommandGuard | None = None,
) -> RecipeResult:
    """레시피의 스텝들을 순차적으로 실행한다.

    ${name} 구문을 사용하여 스텝 내용에 변수를 치환한다.
    첫 번째 실패 스텝에서 실행을 중단한다.

    Args:
        recipe: 실행할 레시피 정의
        variables: 스텝 내용에 치환할 변수 딕셔너리
        timeout: 각 명령의 최대 실행 시간 (초)
        command_guard: 위험 명령 차단을 위한 가드 (None이면 검사 생략)

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

    step_results: list[StepResult] = []

    for step in recipe.steps:
        content = _substitute_variables(step.content, variables)

        if step.step_type == StepType.COMMAND:
            result = await _execute_command(
                step.name, content, timeout, command_guard=command_guard,
            )
        elif step.step_type == StepType.PROMPT:
            # PROMPT 스텝은 변수 치환된 프롬프트 텍스트만 반환
            # (실제 LLM 호출은 호출자의 책임)
            result = StepResult(
                step_name=step.name,
                success=True,
                output=content,
            )
        else:
            result = StepResult(
                step_name=step.name,
                success=False,
                error=f"Unknown step type: {step.step_type}",
            )

        step_results.append(result)

        if not result.success:
            return RecipeResult(
                recipe_name=recipe.name,
                success=False,
                step_results=step_results,
                failed_step=step.name,
                error=result.error,
            )

    return RecipeResult(
        recipe_name=recipe.name,
        success=True,
        step_results=step_results,
    )


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


async def _execute_command(
    name: str,
    command: str,
    timeout: int,
    command_guard: CommandGuard | None = None,
) -> StepResult:
    """셸 명령을 실행하고 결과를 반환한다.

    Args:
        name: 스텝 이름 (결과 추적용)
        command: 실행할 셸 명령 문자열
        timeout: 최대 실행 시간 (초)
        command_guard: 위험 명령 차단 가드

    Returns:
        실행 결과를 담은 StepResult
    """
    # 보안: 위험 명령 패턴 검사
    if command_guard is not None:
        try:
            command_guard.check(command)
        except DangerousCommandError as exc:
            return StepResult(
                step_name=name,
                success=False,
                error=f"Command blocked (dangerous pattern): {exc.description}",
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
        await kill_process_group(process)
        return StepResult(
            step_name=name,
            success=False,
            error=f"Command timed out after {timeout}s",
        )

    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        return StepResult(
            step_name=name,
            success=False,
            output=output,
            error=f"Exit code {process.returncode}: {error}",
        )

    return StepResult(step_name=name, success=True, output=output)
