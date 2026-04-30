"""스킬 스크립트 실행기: 비동기 서브프로세스를 통해 스킬을 실행한다.

동작 흐름:
1. 스킬의 script_path 유효성을 검증
2. 파일 확장자에 따라 적절한 인터프리터로 실행 명령 구성
3. 비동기 서브프로세스로 실행하고, 타임아웃 및 에러를 처리

설계 결정:
- 보안을 위해 filter_env()로 환경 변수를 필터링하고, 프로세스 그룹을 분리
- 타임아웃 시 프로세스 그룹 전체를 종료하여 좀비 프로세스를 방지
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.security import filter_env, get_preexec_fn, kill_process_group
from simpleclaw.skills.models import (
    SkillDefinition,
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

    Args:
        skill: 실행할 스킬 정의
        args: 스크립트에 전달할 선택적 인자 목록
        timeout: 최대 실행 시간 (초)
        metrics: 타임아웃 시 ``kill_process_group`` 결과를 누적할 메트릭 수집기.
            None이면 기록되지 않으며 기존 동작과 호환된다.

    Returns:
        출력, 종료 코드, 에러 정보를 포함하는 SkillResult

    Raises:
        SkillNotFoundError: 스크립트 경로가 없거나 파일이 존재하지 않을 때
        SkillTimeoutError: 실행 시간이 timeout을 초과할 때
        SkillExecutionError: 스크립트가 비정상 종료(exit code != 0)할 때
    """
    if not skill.script_path:
        raise SkillNotFoundError(f"Skill '{skill.name}' has no script path defined")

    script = Path(skill.script_path)
    if not script.is_file():
        raise SkillNotFoundError(
            f"Script not found for skill '{skill.name}': {skill.script_path}"
        )

    # 파일 확장자에 따라 실행 명령 구성
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
