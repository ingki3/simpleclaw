"""서브에이전트 스포너: 서브에이전트 생성, 관리, 통신.

서브프로세스로 서브에이전트를 생성하고, 동시성 풀·워크스페이스·권한 범위를 관리한다.
- 풀 슬롯 획득 → 워크스페이스 생성 → 환경변수로 권한 범위 주입 → 프로세스 실행
- stdout의 JSON을 파싱하여 SubAgentResult로 반환
- 타임아웃 시 SIGTERM → 유예 기간 후 SIGKILL 순서로 종료
"""

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
from simpleclaw.logging.trace_context import inject_trace_id_env

logger = logging.getLogger(__name__)

_GRACE_PERIOD = 5  # SIGTERM 후 SIGKILL까지 유예 시간(초)


class SubAgentSpawner:
    """서브에이전트 서브프로세스를 생성하고 관리한다.

    동시성 풀, 워크스페이스 매니저, 기본 권한 범위를 config에서 초기화한다.
    """

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
        """서브에이전트를 생성하고 결과를 대기한다.

        풀 슬롯 획득(풀이 가득 차면 대기) → 워크스페이스 생성 →
        권한 범위 주입 → 커맨드 실행 → JSON 출력 파싱 후 결과 반환.
        """
        agent_id = str(uuid.uuid4())[:8]
        effective_scope = scope or self._default_scope
        effective_timeout = timeout or self._default_timeout

        # 워크스페이스 생성 후 권한 범위에 추가
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

        # 풀 슬롯 획득
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
        """Python 스크립트를 서브에이전트로 실행하는 편의 메서드."""
        import sys

        return await self.spawn(
            command=[sys.executable, script_path],
            task=task,
            scope=scope,
            timeout=timeout,
        )

    def get_running(self) -> list[SubAgent]:
        """현재 실행 중인 서브에이전트 목록을 반환한다."""
        return list(self._running_agents.values())

    def get_pool_status(self) -> dict:
        """동시성 풀의 현재 상태를 반환한다."""
        return self._pool.get_status()

    async def shutdown(self) -> None:
        """실행 중인 모든 서브에이전트를 안전하게 종료한다."""
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
        """서브에이전트 프로세스를 실행하고 결과를 파싱한다."""
        start_time = datetime.now()

        # 환경변수에 권한 범위·에이전트 ID·워크스페이스 경로·trace_id 주입.
        # trace_id는 같은 사용자 메시지에서 출발한 모든 서브에이전트가
        # 동일 식별자로 로그를 남길 수 있도록 부모 컨텍스트에서 가져온다.
        env = inject_trace_id_env(os.environ.copy())
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

            # stdout JSON 파싱
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

                # 실패 시에도 stdout에 JSON이 있으면 파싱 시도
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

            # 성공 경로 — JSON 파싱
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
