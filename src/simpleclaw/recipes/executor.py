"""레시피 단계별 실행기.

레시피에 정의된 스텝들을 순차적으로 실행하며, 변수 치환과 보안 검사를 수행한다.

동작 흐름:
1. 필수 파라미터 검증 및 기본값 적용
2. 각 스텝의 content에 ${변수명} 패턴을 실제 값으로 치환
3. COMMAND 스텝은 셸 명령으로 실행, PROMPT 스텝은 텍스트만 반환
4. 첫 번째 실패 스텝에서 실행 중단

설계 결정:
- PROMPT 스텝의 LLM 호출은 호출자의 책임 (관심사 분리)
- CommandGuard를 통한 위험 명령 차단으로 보안 강화
- 프로세스 그룹 분리 및 환경 변수 필터링으로 격리 실행
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
