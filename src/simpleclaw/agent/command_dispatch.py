"""명령 실행 dispatch 경계.

오케스트레이터는 대화 상태와 tool loop 흐름만 보유하고, 셸 명령 실행의 보안
검사·정규화·fallback·timeout 처리는 이 모듈이 담당한다. 기존 private method를
얇은 호환 래퍼로 남기기 위해 orchestrator 인스턴스를 명시적으로 받아 필요한
런타임 설정만 참조한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from simpleclaw.llm.models import ToolCall
from simpleclaw.security import (
    DangerousCommandError,
    filter_env,
    get_preexec_fn,
    kill_process_group,
)
from simpleclaw.security.sanitize import sanitize_tool_error

logger = logging.getLogger(__name__)

_AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE = (
    "Error: composite `agent-browser` chains are blocked. Each agent-browser "
    "step must be a SEPARATE tool call (one `execute_skill` per `open`, `wait`, "
    "`get`/`evaluate` step). For plain page text, prefer `web_fetch` — it already "
    "auto-falls back to a headless browser. If `web_fetch` returned a short body "
    "for this URL, the site is blocking automated fetching; do NOT keep trying "
    "the same URL via agent-browser. Reply to the user that the page cannot be "
    "retrieved instead."
)


def resolve_command_timeout(orchestrator: Any, command: str) -> int:
    """명령 문자열에 따라 실제로 적용할 타임아웃(초) 을 결정한다."""
    if "agent-browser " in command or command.endswith("agent-browser"):
        if orchestrator._agent_browser_timeout > orchestrator._skill_timeout:
            return orchestrator._agent_browser_timeout
    return orchestrator._skill_timeout


def call_invokes_agent_browser(tool_call: ToolCall) -> bool:
    """tool_call 이 ``agent-browser`` CLI 를 실행하는지 판별한다."""
    name = tool_call.name
    args = tool_call.arguments or {}
    if name == "execute_skill":
        if args.get("skill_name") == "agent-browser":
            return True
        cmd = str(args.get("command") or "")
        if is_agent_browser_command(cmd):
            return True
        inner_args = str(args.get("args") or "")
        return is_agent_browser_command(inner_args)
    if name == "cli":
        cmd = str(args.get("command") or "")
        return is_agent_browser_command(cmd)
    return False


def is_agent_browser_command(command: str) -> bool:
    """``command`` 가 ``agent-browser`` CLI 를 호출하는지 판별한다."""
    return "agent-browser " in command or command.endswith("agent-browser")


def is_composite_agent_browser_chain(command: str) -> bool:
    """``agent-browser`` 가 ``&&``/``||``/``;`` 로 묶인 composite chain 인지 판별."""
    if not is_agent_browser_command(command):
        return False
    return ("&&" in command) or ("||" in command) or (";" in command)


def agent_browser_npx_fallback_command(command: str, stderr: str) -> str | None:
    """bare ``agent-browser`` PATH 실패를 ``npx --yes agent-browser`` 로 보정."""
    if not is_agent_browser_command(command):
        return None
    stripped = command.lstrip()
    if stripped.startswith("npx --yes agent-browser"):
        return None
    if "command not found" not in stderr.lower():
        return None
    if stripped == "agent-browser":
        return command.replace("agent-browser", "npx --yes agent-browser", 1)
    if stripped.startswith("agent-browser "):
        leading_len = len(command) - len(stripped)
        return (
            command[:leading_len]
            + stripped.replace("agent-browser", "npx --yes agent-browser", 1)
        )
    return None


async def execute_command(orchestrator: Any, skill_name: str, command: str) -> str:
    """셸 명령을 실행하고 출력/오류를 tool-safe 문자열로 반환한다."""
    try:
        orchestrator._command_guard.check(command)
    except DangerousCommandError as exc:
        logger.warning("Command blocked for skill '%s': %s", skill_name, exc)
        return f"Command blocked (dangerous pattern detected): {exc.description}"

    if is_composite_agent_browser_chain(command):
        logger.warning(
            "BIZ-190: composite agent-browser chain blocked for skill '%s': %s",
            skill_name,
            command[:200],
        )
        return _AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE

    command = orchestrator._normalize_skill_command(command)
    effective_timeout = resolve_command_timeout(orchestrator, command)

    logger.info(
        "Executing skill command (timeout=%ds): %s",
        effective_timeout,
        command,
    )
    try:
        orchestrator._workspace_dir.mkdir(parents=True, exist_ok=True)
        env = filter_env(passthrough=orchestrator._env_passthrough)
        env["AGENT_WORKSPACE"] = str(orchestrator._workspace_dir.resolve())

        async def _run_shell_once(run_command: str) -> tuple[int, str, str]:
            """한 번의 shell 실행 결과를 구조화해서 반환한다."""
            proc = await asyncio.create_subprocess_shell(
                run_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(orchestrator._workspace_dir),
                env=env,
                preexec_fn=get_preexec_fn(),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=effective_timeout
                )
            except asyncio.TimeoutError:
                await kill_process_group(proc, metrics=orchestrator._metrics)
                raise
            return (
                int(proc.returncode or 0),
                stdout.decode("utf-8", errors="replace").strip(),
                stderr.decode("utf-8", errors="replace").strip(),
            )

        returncode, output, error = await _run_shell_once(command)

        if returncode != 0:
            fallback_command = agent_browser_npx_fallback_command(command, error)
            if fallback_command is not None:
                logger.warning(
                    "BIZ-337: agent-browser command not found; retrying via npx: %s",
                    fallback_command,
                )
                fb_returncode, fb_output, fb_error = await _run_shell_once(
                    fallback_command,
                )
                if fb_returncode == 0:
                    logger.info(
                        "BIZ-337: agent-browser npx fallback succeeded: %d chars output",
                        len(fb_output),
                    )
                    return fb_output if fb_output else "[Command completed with no output]"
                logger.error(
                    "BIZ-337: agent-browser npx fallback failed (exit %d): %s",
                    fb_returncode,
                    fb_error,
                )
                return sanitize_tool_error(
                    "agent-browser failed because the bare command was not on PATH, "
                    "and the npx fallback also failed. Do not ask the user to search "
                    "manually; report that live browser retrieval is unavailable and "
                    "separate verified facts from unverified facts. "
                    f"Original: Command failed (exit {returncode}): {error[:300]} | "
                    f"Fallback: Command failed (exit {fb_returncode}): {fb_error[:300]}"
                )
            logger.error("Skill command failed (exit %d): %s", returncode, error)
            return sanitize_tool_error(
                f"Command failed (exit {returncode}): {error[:500]}"
            )

        logger.info("Skill command succeeded: %d chars output", len(output))
        return output if output else "[Command completed with no output]"

    except asyncio.TimeoutError:
        logger.error("Skill command timed out: %s", command)
        return f"Command timed out after {effective_timeout}s"
    except Exception as exc:  # noqa: BLE001 — tool loop를 죽이지 않고 오류 문자열 반환.
        logger.error("Skill command error: %s", exc)
        return sanitize_tool_error(f"Command error: {str(exc)[:200]}")
