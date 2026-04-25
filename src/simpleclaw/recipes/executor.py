"""Recipe step-by-step executor."""

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
    """Execute a recipe's steps sequentially.

    Variables are substituted into step content using ${name} syntax.
    Execution stops on the first failed step.
    """
    variables = variables or {}

    # Validate required parameters
    for param in recipe.parameters:
        if param.required and param.name not in variables:
            if param.default:
                variables[param.name] = param.default
            else:
                raise RecipeExecutionError(
                    f"Required parameter '{param.name}' not provided "
                    f"for recipe '{recipe.name}'"
                )

    # Apply defaults for optional parameters
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
            # Prompt steps return the resolved prompt text
            # (actual LLM call is the caller's responsibility)
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
    """Replace ${variable_name} patterns with values."""
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
    """Execute a shell command and return the result."""
    # Security: check for dangerous commands
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
