"""Sub-agent spawner: spawn, manage, and communicate with sub-agents."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from simpleclaw.agents.models import (
    PermissionScope,
    SpawnError,
    SubAgent,
    SubAgentResult,
    SubAgentStatus,
)
from simpleclaw.agents.pool import ConcurrencyPool
from simpleclaw.agents.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

_GRACE_PERIOD = 5  # seconds before SIGKILL after SIGTERM


class SubAgentSpawner:
    """Spawns and manages sub-agent subprocesses."""

    def __init__(self, config: dict) -> None:
        self._config = config
        self._pool = ConcurrencyPool(
            max_concurrent=config.get("max_concurrent", 3)
        )
        self._workspace = WorkspaceManager(
            base_dir=config.get("workspace_dir", "workspace/sub_agents"),
            cleanup=config.get("cleanup_workspace", False),
        )
        self._default_scope = PermissionScope(
            allowed_paths=config.get("default_scope", {}).get(
                "allowed_paths", []
            ),
            network=config.get("default_scope", {}).get("network", False),
        )
        self._default_timeout = config.get("default_timeout", 300)
        self._running_agents: dict[str, SubAgent] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def spawn(
        self,
        command: list[str],
        task: str,
        scope: PermissionScope | None = None,
        timeout: int | None = None,
    ) -> SubAgentResult:
        """Spawn a sub-agent and wait for its result.

        Acquires a pool slot (blocks if pool is full), creates workspace,
        injects permission scope, runs the command, and parses JSON output.
        """
        agent_id = str(uuid.uuid4())[:8]
        effective_scope = scope or self._default_scope
        effective_timeout = timeout or self._default_timeout

        # Create workspace and add to scope
        workspace = self._workspace.create(agent_id)
        scope_with_workspace = PermissionScope(
            allowed_paths=[str(workspace)] + effective_scope.allowed_paths,
            network=effective_scope.network,
        )

        agent = SubAgent(
            agent_id=agent_id,
            task=task,
            command=command,
            scope=scope_with_workspace,
            workspace_path=workspace,
            timeout=effective_timeout,
        )

        # Acquire pool slot
        await self._pool.acquire()
        agent.status = SubAgentStatus.RUNNING
        agent.spawn_time = datetime.now()
        self._running_agents[agent_id] = agent

        try:
            result = await self._execute(agent)
        finally:
            self._pool.release()
            self._running_agents.pop(agent_id, None)
            self._processes.pop(agent_id, None)

            if self._workspace.should_cleanup:
                self._workspace.cleanup(agent_id)

        return result

    async def spawn_python(
        self,
        script_path: str,
        task: str,
        scope: PermissionScope | None = None,
        timeout: int | None = None,
    ) -> SubAgentResult:
        """Convenience method to spawn a Python script as a sub-agent."""
        import sys

        return await self.spawn(
            command=[sys.executable, script_path],
            task=task,
            scope=scope,
            timeout=timeout,
        )

    def get_running(self) -> list[SubAgent]:
        return list(self._running_agents.values())

    def get_pool_status(self) -> dict:
        return self._pool.get_status()

    async def shutdown(self) -> None:
        """Gracefully terminate all running sub-agents."""
        for agent_id, proc in list(self._processes.items()):
            agent = self._running_agents.get(agent_id)
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_GRACE_PERIOD)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

                if agent:
                    agent.status = SubAgentStatus.KILLED
                    agent.end_time = datetime.now()

                logger.info("Shutdown: terminated sub-agent %s", agent_id)
            except ProcessLookupError:
                pass

        self._running_agents.clear()
        self._processes.clear()

    async def _execute(self, agent: SubAgent) -> SubAgentResult:
        """Execute the sub-agent process and parse results."""
        start_time = datetime.now()

        # Prepare environment with permission scope
        env = os.environ.copy()
        env["AGENT_SCOPE"] = json.dumps(agent.scope.to_dict())
        env["AGENT_ID"] = agent.agent_id
        if agent.workspace_path:
            env["AGENT_WORKSPACE"] = str(agent.workspace_path)

        try:
            proc = await asyncio.create_subprocess_exec(
                *agent.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(agent.workspace_path) if agent.workspace_path else None,
            )
            self._processes[agent.agent_id] = proc

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=agent.timeout
                )
            except asyncio.TimeoutError:
                agent.status = SubAgentStatus.TIMEOUT
                agent.end_time = datetime.now()
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_GRACE_PERIOD)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

                elapsed = (datetime.now() - start_time).total_seconds()
                logger.warning(
                    "Sub-agent %s timed out after %.1fs", agent.agent_id, elapsed
                )
                return SubAgentResult(
                    agent_id=agent.agent_id,
                    status="error",
                    error=f"Timeout after {agent.timeout}s",
                    exit_code=-1,
                    execution_time=elapsed,
                )

            elapsed = (datetime.now() - start_time).total_seconds()
            agent.exit_code = proc.returncode
            agent.end_time = datetime.now()

            # Parse JSON output
            stdout_text = stdout.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                agent.status = SubAgentStatus.FAILURE
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "Sub-agent %s failed (exit=%d): %s",
                    agent.agent_id,
                    proc.returncode,
                    stderr_text[:500],
                )

                # Try to parse stdout as JSON even on failure
                try:
                    parsed = json.loads(stdout_text) if stdout_text else {}
                    return SubAgentResult(
                        agent_id=agent.agent_id,
                        status=parsed.get("status", "error"),
                        data=parsed.get("data"),
                        error=parsed.get("error", stderr_text[:500]),
                        exit_code=proc.returncode,
                        execution_time=elapsed,
                    )
                except json.JSONDecodeError:
                    return SubAgentResult(
                        agent_id=agent.agent_id,
                        status="error",
                        error=stderr_text[:500] or f"Exit code {proc.returncode}",
                        exit_code=proc.returncode,
                        execution_time=elapsed,
                    )

            # Success path — parse JSON
            if not stdout_text:
                agent.status = SubAgentStatus.SUCCESS
                return SubAgentResult(
                    agent_id=agent.agent_id,
                    status="success",
                    data={},
                    exit_code=0,
                    execution_time=elapsed,
                )

            try:
                parsed = json.loads(stdout_text)
                agent.status = SubAgentStatus.SUCCESS
                return SubAgentResult(
                    agent_id=agent.agent_id,
                    status=parsed.get("status", "success"),
                    data=parsed.get("data"),
                    error=parsed.get("error"),
                    exit_code=0,
                    execution_time=elapsed,
                )
            except json.JSONDecodeError as exc:
                agent.status = SubAgentStatus.FAILURE
                logger.error(
                    "Sub-agent %s produced invalid JSON: %s",
                    agent.agent_id,
                    str(exc),
                )
                return SubAgentResult(
                    agent_id=agent.agent_id,
                    status="error",
                    error=f"Invalid JSON output: {exc}",
                    exit_code=0,
                    execution_time=elapsed,
                )

        except FileNotFoundError:
            agent.status = SubAgentStatus.FAILURE
            agent.end_time = datetime.now()
            elapsed = (datetime.now() - start_time).total_seconds()
            raise SpawnError(
                f"Command not found: {agent.command[0]}"
            )
        except Exception as exc:
            agent.status = SubAgentStatus.FAILURE
            agent.end_time = datetime.now()
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.exception("Sub-agent %s error", agent.agent_id)
            return SubAgentResult(
                agent_id=agent.agent_id,
                status="error",
                error=str(exc),
                exit_code=-1,
                execution_time=elapsed,
            )
