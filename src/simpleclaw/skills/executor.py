"""Skill script executor via async subprocess."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from simpleclaw.security import filter_env, get_preexec_fn, kill_process_group
from simpleclaw.skills.models import (
    SkillDefinition,
    SkillExecutionError,
    SkillNotFoundError,
    SkillResult,
    SkillTimeoutError,
)

logger = logging.getLogger(__name__)


async def execute_skill(
    skill: SkillDefinition,
    args: list[str] | None = None,
    timeout: int = 60,
) -> SkillResult:
    """Execute a skill's target script as an async subprocess.

    Args:
        skill: The skill definition to execute.
        args: Optional arguments to pass to the script.
        timeout: Maximum execution time in seconds.

    Returns:
        SkillResult with output, exit code, and error info.
    """
    if not skill.script_path:
        raise SkillNotFoundError(f"Skill '{skill.name}' has no script path defined")

    script = Path(skill.script_path)
    if not script.is_file():
        raise SkillNotFoundError(
            f"Script not found for skill '{skill.name}': {skill.script_path}"
        )

    # Determine how to run the script
    cmd = _build_command(script)
    if args:
        cmd.extend(args)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=skill.skill_dir or None,
            env=filter_env(),
            preexec_fn=get_preexec_fn(),
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await kill_process_group(process)
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
    """Build the command to execute a script based on its extension."""
    suffix = script.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(script)]
    elif suffix == ".sh":
        return ["bash", str(script)]
    elif suffix == ".js":
        return ["node", str(script)]
    else:
        # Try to run it directly (must have execute permission)
        return [str(script)]
