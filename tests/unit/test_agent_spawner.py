"""Tests for the sub-agent spawner."""

import asyncio
import json
import sys
import textwrap

import pytest

from simpleclaw.agents.models import PermissionScope, SpawnError
from simpleclaw.agents.spawner import SubAgentSpawner


def _write_script(tmp_path, name, code):
    """Write a Python script to tmp_path and return its path."""
    script = tmp_path / name
    script.write_text(textwrap.dedent(code))
    return str(script)


@pytest.fixture
def config(tmp_path):
    return {
        "max_concurrent": 2,
        "default_timeout": 10,
        "workspace_dir": str(tmp_path / "workspaces"),
        "cleanup_workspace": True,
        "default_scope": {
            "allowed_paths": [],
            "network": False,
        },
    }


class TestSubAgentSpawner:
    @pytest.mark.asyncio
    async def test_spawn_success(self, config, tmp_path):
        script = _write_script(tmp_path, "success.py", '''
            import json
            print(json.dumps({"status": "success", "data": {"answer": 42}}))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="test task",
        )
        assert result.status == "success"
        assert result.data == {"answer": 42}
        assert result.exit_code == 0
        assert result.execution_time > 0

    @pytest.mark.asyncio
    async def test_spawn_failure(self, config, tmp_path):
        script = _write_script(tmp_path, "fail.py", '''
            import sys
            sys.stderr.write("something went wrong\\n")
            sys.exit(1)
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="failing task",
        )
        assert result.status == "error"
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_spawn_invalid_json(self, config, tmp_path):
        script = _write_script(tmp_path, "bad_json.py", '''
            print("this is not json")
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="bad json task",
        )
        assert result.status == "error"
        assert "Invalid JSON" in result.error

    @pytest.mark.asyncio
    async def test_spawn_timeout(self, config, tmp_path):
        config["default_timeout"] = 1
        script = _write_script(tmp_path, "slow.py", '''
            import time
            time.sleep(10)
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="slow task",
        )
        assert result.status == "error"
        assert "Timeout" in result.error

    @pytest.mark.asyncio
    async def test_spawn_not_found(self, config):
        spawner = SubAgentSpawner(config)
        with pytest.raises(SpawnError, match="Command not found"):
            await spawner.spawn(
                command=["nonexistent_command_xyz"],
                task="missing command",
            )

    @pytest.mark.asyncio
    async def test_pool_status(self, config):
        spawner = SubAgentSpawner(config)
        status = spawner.get_pool_status()
        assert status["running"] == 0
        assert status["max"] == 2

    @pytest.mark.asyncio
    async def test_permission_scope_injected(self, config, tmp_path):
        script = _write_script(tmp_path, "scope.py", '''
            import json, os
            scope = json.loads(os.environ.get("AGENT_SCOPE", "{}"))
            print(json.dumps({"status": "success", "data": scope}))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="scope test",
            scope=PermissionScope(allowed_paths=["/data"], network=True),
        )
        assert result.status == "success"
        assert "/data" in result.data["allowed_paths"]
        assert result.data["network"] is True

    @pytest.mark.asyncio
    async def test_default_scope_applied(self, config, tmp_path):
        script = _write_script(tmp_path, "default_scope.py", '''
            import json, os
            scope = json.loads(os.environ.get("AGENT_SCOPE", "{}"))
            print(json.dumps({"status": "success", "data": scope}))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="default scope test",
        )
        assert result.status == "success"
        assert result.data["network"] is False

    @pytest.mark.asyncio
    async def test_concurrency_limit(self, config, tmp_path):
        config["max_concurrent"] = 2
        script = _write_script(tmp_path, "slow2.py", '''
            import json, time
            time.sleep(0.3)
            print(json.dumps({"status": "success", "data": {}}))
        ''')
        spawner = SubAgentSpawner(config)
        # Spawn 4 tasks with limit of 2
        tasks = [
            spawner.spawn([sys.executable, script], f"task-{i}")
            for i in range(4)
        ]
        results = await asyncio.gather(*tasks)
        assert all(r.status == "success" for r in results)

    @pytest.mark.asyncio
    async def test_shutdown(self, config, tmp_path):
        config["max_concurrent"] = 2
        script = _write_script(tmp_path, "long.py", '''
            import time
            time.sleep(30)
        ''')
        spawner = SubAgentSpawner(config)

        # Start a task in background
        task = asyncio.create_task(
            spawner.spawn([sys.executable, script], "long task")
        )
        await asyncio.sleep(0.2)  # Let it start
        await spawner.shutdown()
        # The task should complete (with killed status)
        result = await task
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_get_running(self, config, tmp_path):
        script = _write_script(tmp_path, "running.py", '''
            import json, time
            time.sleep(0.5)
            print(json.dumps({"status": "success", "data": {}}))
        ''')
        spawner = SubAgentSpawner(config)
        task = asyncio.create_task(
            spawner.spawn([sys.executable, script], "running task")
        )
        await asyncio.sleep(0.1)
        running = spawner.get_running()
        assert len(running) == 1
        assert running[0].task == "running task"
        await task

    @pytest.mark.asyncio
    async def test_empty_stdout(self, config, tmp_path):
        script = _write_script(tmp_path, "empty.py", '''
            pass
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="empty output",
        )
        assert result.status == "success"
        assert result.data == {}

    @pytest.mark.asyncio
    async def test_meta_propagated_from_subagent(self, config, tmp_path):
        """서브에이전트가 응답 meta에 첨부한 정보가 SubAgentResult.meta로 전달되어야 한다."""
        script = _write_script(tmp_path, "with_meta.py", '''
            import json
            print(json.dumps({
                "status": "success",
                "data": {"v": 1},
                "meta": {"agent_id": "abc", "version": "1.0"},
            }))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="meta test",
        )
        assert result.status == "success"
        assert result.meta == {"agent_id": "abc", "version": "1.0"}

    @pytest.mark.asyncio
    async def test_schema_violation_missing_status(self, config, tmp_path):
        """status 필드가 없는 응답은 안전한 에러로 변환되어야 한다."""
        script = _write_script(tmp_path, "no_status.py", '''
            import json
            print(json.dumps({"data": {"x": 1}}))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="missing status",
        )
        assert result.status == "error"
        assert result.meta is not None
        assert result.meta["validation_failure"]["reason"] == "schema_violation"

    @pytest.mark.asyncio
    async def test_schema_violation_invalid_status(self, config, tmp_path):
        """알 수 없는 status 값은 schema_violation으로 분류된다."""
        script = _write_script(tmp_path, "bad_status.py", '''
            import json
            print(json.dumps({"status": "weird", "data": {}}))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="invalid status",
        )
        assert result.status == "error"
        assert result.meta["validation_failure"]["reason"] == "schema_violation"

    @pytest.mark.asyncio
    async def test_top_level_array_rejected(self, config, tmp_path):
        """최상위가 객체가 아닌 응답은 거부되어야 한다."""
        script = _write_script(tmp_path, "array.py", '''
            import json
            print(json.dumps([{"status": "success"}]))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="array output",
        )
        assert result.status == "error"
        assert result.meta["validation_failure"]["reason"] == "schema_violation"

    @pytest.mark.asyncio
    async def test_logical_error_response_preserved(self, config, tmp_path):
        """exit=0이지만 status=error인 응답은 그대로 전달되어야 한다."""
        script = _write_script(tmp_path, "logical_err.py", '''
            import json
            print(json.dumps({
                "status": "error",
                "error": {"code": "E_INPUT", "message": "bad input"},
            }))
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="logical error",
        )
        assert result.status == "error"
        assert result.exit_code == 0
        assert "E_INPUT" in result.error
        assert "bad input" in result.error

    @pytest.mark.asyncio
    async def test_failure_path_with_valid_json(self, config, tmp_path):
        """exit_code != 0이지만 stdout JSON이 유효하면 그 정보를 살린다."""
        script = _write_script(tmp_path, "exit_with_json.py", '''
            import json, sys
            print(json.dumps({
                "status": "error",
                "error": "explicit failure",
            }))
            sys.exit(2)
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="exit with json",
        )
        assert result.status == "error"
        assert result.exit_code == 2
        assert result.error == "explicit failure"

    @pytest.mark.asyncio
    async def test_failure_path_with_invalid_json(self, config, tmp_path):
        """exit_code != 0이고 stdout이 비유효 JSON이면 stderr 기반 에러로 폴백한다."""
        script = _write_script(tmp_path, "exit_invalid.py", '''
            import sys
            print("not json at all")
            sys.stderr.write("crash details\\n")
            sys.exit(3)
        ''')
        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="exit invalid json",
        )
        assert result.status == "error"
        assert result.exit_code == 3
        assert "crash details" in result.error
        # 검증 실패 진단 정보가 meta에 포함되어야 한다.
        assert result.meta is not None
        assert result.meta["validation_failure"]["reason"] == "invalid_json"

    @pytest.mark.asyncio
    async def test_trace_id_propagated_to_subagent(self, config, tmp_path):
        """부모 컨텍스트의 trace_id가 SIMPLECLAW_TRACE_ID로 전달되어야 한다."""
        from simpleclaw.logging.trace_context import (
            TRACE_ID_ENV_VAR,
            trace_scope,
        )

        script = _write_script(tmp_path, "trace_echo.py", f'''
            import json, os
            print(json.dumps({{
                "status": "success",
                "data": {{"trace": os.environ.get({TRACE_ID_ENV_VAR!r}, "")}},
            }}))
        ''')
        spawner = SubAgentSpawner(config)

        with trace_scope("agent-trace-id"):
            result = await spawner.spawn(
                command=[sys.executable, script],
                task="trace propagation",
            )
        assert result.status == "success"
        assert result.data["trace"] == "agent-trace-id"
