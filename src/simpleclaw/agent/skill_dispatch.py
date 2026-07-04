"""스킬 실행·프롬프트 포맷 dispatch 경계.

스킬 목록을 LLM 프롬프트에 노출하는 형식, command 정규화, 등록 스킬 실행을
오케스트레이터 밖으로 분리한다. 공개 semantic은 기존 private method와 동일하게
유지하며 orchestrator는 호환용 래퍼만 제공한다.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any

from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.skills.executor import execute_skill as run_skill
from simpleclaw.skills.models import SkillDefinition

logger = logging.getLogger(__name__)


def format_skills_for_prompt(skills: list[SkillDefinition]) -> str:
    """시스템 프롬프트용 스킬 개요 목록을 생성한다."""
    if not skills:
        return ""
    lines = [*load_system_prompt("skill_listing").prompt.splitlines(), ""]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
        script_path = Path(skill.script_path) if skill.script_path else None
        if script_path is not None and script_path.suffix == ".py" and script_path.is_file():
            lines.append(
                f"  Invocation: `execute_skill(skill_name=\"{skill.name}\", "
                f"args=\"<positional args>\")`"
            )
        else:
            lines.append(
                f"  Invocation: call `skill_docs(\"{skill.name}\")` first to "
                "read the exact command sequence."
            )
    return "\n".join(lines)


def normalize_skill_command(orchestrator: Any, command: str) -> str:
    """셸 명령을 실행 가능한 형태로 정규화한다."""
    parts = command.split(None, 1)
    if not parts:
        return command

    first_token, rest = parts[0], parts[1] if len(parts) > 1 else ""

    skill = getattr(orchestrator, "_skills_by_name", {}).get(first_token)
    if skill is not None and skill.script_path:
        rewritten = _venv_direct_command(skill, rest)
        if rewritten is not None:
            logger.info(
                "BIZ-166: rewrote bare skill invocation '%s' → venv-direct",
                first_token,
            )
            return rewritten

    prefix_runner = None
    prefix_skip = 0
    if first_token == "uvx":
        prefix_runner = "uvx"
        prefix_skip = 1
    elif first_token == "pipx" and rest.split(None, 1)[:1] == ["run"]:
        prefix_runner = "pipx run"
        prefix_skip = 2

    if prefix_runner is not None:
        inner_tokens = command.split(None, prefix_skip + 1)
        if len(inner_tokens) >= prefix_skip + 1:
            inner_first = inner_tokens[prefix_skip]
            inner_rest = (
                inner_tokens[prefix_skip + 1]
                if len(inner_tokens) > prefix_skip + 1
                else ""
            )
            inner_skill = getattr(orchestrator, "_skills_by_name", {}).get(inner_first)
            if inner_skill is not None and inner_skill.script_path:
                rewritten = _venv_direct_command(inner_skill, inner_rest)
                if rewritten is not None:
                    logger.info(
                        "BIZ-166: rewrote '%s %s ...' → venv-direct",
                        prefix_runner,
                        inner_first,
                    )
                    return rewritten

    if first_token not in ("python", "python3"):
        return command

    try:
        tokens = shlex.split(rest)
    except ValueError:
        tokens = rest.split()

    script_path = None
    for token in tokens:
        if token.endswith(".py") and Path(token).is_file():
            script_path = Path(token)
            break

    if script_path is None:
        if first_token == "python":
            return f"python3 {rest}"
        return command

    venv_python = find_venv_python(script_path)
    if venv_python is not None:
        return f"{venv_python} {rest}"

    if first_token == "python":
        return f"python3 {rest}"
    return command


def _venv_direct_command(skill: SkillDefinition, rest: str) -> str | None:
    """등록된 .py skill을 인근 venv python 직접 호출 문자열로 변환한다."""
    script_path = Path(skill.script_path or "")
    if script_path.suffix != ".py" or not script_path.is_file():
        return None
    venv_python = find_venv_python(script_path)
    if venv_python is None:
        return None
    return f"{venv_python} {script_path} {rest}".rstrip()


def find_venv_python(script_path: Path) -> Path | None:
    """스크립트 인근 venv 의 python 실행 파일 경로를 찾는다."""
    for venv_dir in (
        script_path.parent / "venv",
        script_path.parent.parent / "venv",
        script_path.parent / ".venv",
        script_path.parent.parent / ".venv",
    ):
        venv_python = venv_dir / "bin" / "python"
        if venv_python.is_file():
            return venv_python
    return None


async def dispatch_external_skill(orchestrator: Any, args: dict) -> str:
    """execute_skill 도구 호출을 처리한다."""
    skill_name = str(args.get("skill_name", "") or "").strip()
    command = str(args.get("command", "") or "").strip()
    skill_args = str(args.get("args", "") or "")
    if skill_name and orchestrator._resolve_skill_name(skill_name) is not None:
        if not skill_args and command:
            skill_args = _extract_registered_skill_args_from_command(skill_name, command)
        result = await execute_registered_skill(orchestrator, skill_name, skill_args)
        return result or "[no output]"
    if command:
        return await orchestrator._execute_command(skill_name, command)
    result = await execute_registered_skill(orchestrator, skill_name, skill_args)
    return result or "[no output]"


def _parse_skill_args(args_str: str) -> list[str] | None:
    """등록 스킬 args 문자열을 shell quoting 규칙으로 파싱한다.

    닫히지 않은 quote 등 LLM 생성 오류는 기존 split 동작으로 fallback 해서
    tool loop 전체를 죽이지 않는다.
    """
    if not args_str:
        return None
    try:
        return shlex.split(args_str)
    except ValueError:
        return args_str.split()


def _extract_registered_skill_args_from_command(skill_name: str, command: str) -> str:
    """legacy ``command`` 필드에서 등록 skill 뒤의 인자만 추출한다.

    모델이 ``skill_name``과 함께 ``command='skill-name "query"'``를 보내도
    등록 skill executor로 normalize하기 위해 shell runner 접두부만 걷어낸다.
    """
    stripped = command.strip()
    if not stripped:
        return ""
    first = stripped.split(None, 1)
    if first[0] == skill_name:
        return first[1] if len(first) > 1 else ""
    uvx_parts = stripped.split(None, 2)
    if uvx_parts[0] == "uvx" and len(uvx_parts) >= 2 and uvx_parts[1] == skill_name:
        return uvx_parts[2] if len(uvx_parts) > 2 else ""
    pipx_parts = stripped.split(None, 3)
    if (
        pipx_parts[0] == "pipx"
        and len(pipx_parts) >= 3
        and pipx_parts[1] == "run"
        and pipx_parts[2] == skill_name
    ):
        return pipx_parts[3] if len(pipx_parts) > 3 else ""
    return stripped


async def execute_registered_skill(orchestrator: Any, skill_name: str, args_str: str) -> str | None:
    """이름으로 스킬을 찾아 실행하고 출력을 반환한다."""
    skill = orchestrator._resolve_skill_name(skill_name)
    if skill is None:
        logger.warning("Skill '%s' not found in registry", skill_name)
        return f"[Skill '{skill_name}' not found. Available: {', '.join(orchestrator._skills_by_name.keys())}]"

    if not skill.script_path:
        skill_md = Path(skill.skill_dir) / "SKILL.md"
        if skill_md.is_file():
            content = skill_md.read_text(encoding="utf-8")[:2000]
            return f"[Skill documentation for {skill_name}]:\n{content}"
        return None

    try:
        args = _parse_skill_args(args_str)
        result = await run_skill(
            skill,
            args=args,
            timeout=orchestrator._skill_timeout,
            metrics=orchestrator._metrics,
            env_passthrough=orchestrator._env_passthrough,
        )
        logger.info("Skill '%s' executed: success=%s", skill_name, result.success)
        return result.output
    except Exception as exc:  # noqa: BLE001 — tool loop를 죽이지 않고 오류 문자열 반환.
        logger.error("Skill '%s' execution failed: %s", skill_name, exc)
        return f"Error executing skill {skill_name}: {str(exc)[:200]}"

