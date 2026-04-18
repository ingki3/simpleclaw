"""Integration tests for the sub-agent pipeline."""

import json
import sys
import textwrap

import pytest

from simpleclaw.agents import SubAgentSpawner, PermissionScope
from simpleclaw.config import load_sub_agents_config


def _write_script(tmp_path, name, code):
    script = tmp_path / name
    script.write_text(textwrap.dedent(code))
    return str(script)


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"""
sub_agents:
  max_concurrent: 2
  default_timeout: 10
  workspace_dir: "{tmp_path}/workspaces"
  cleanup_workspace: false
  default_scope:
    allowed_paths: []
    network: false
""")
    return load_sub_agents_config(config_file)


class TestSubAgentPipeline:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self, config, tmp_path):
        """Test complete spawn → execute → result lifecycle."""
        script = _write_script(tmp_path, "agent.py", '''
            import json, os
            agent_id = os.environ.get("AGENT_ID", "unknown")
            workspace = os.environ.get("AGENT_WORKSPACE", "unknown")
            scope = json.loads(os.environ.get("AGENT_SCOPE", "{}"))
            result = {
                "status": "success",
                "data": {
                    "agent_id": agent_id,
                    "workspace": workspace,
                    "has_scope": bool(scope),
                }
            }
            print(json.dumps(result))
        ''')

        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="integration test",
            scope=PermissionScope(allowed_paths=["/test"], network=False),
        )

        assert result.status == "success"
        assert result.data["has_scope"] is True
        assert result.data["agent_id"] != "unknown"

    @pytest.mark.asyncio
    async def test_workspace_created(self, config, tmp_path):
        """Test that workspace directory is created for sub-agent."""
        script = _write_script(tmp_path, "ws_check.py", '''
            import json, os
            workspace = os.environ.get("AGENT_WORKSPACE", "")
            exists = os.path.isdir(workspace) if workspace else False
            print(json.dumps({"status": "success", "data": {"workspace_exists": exists}}))
        ''')

        spawner = SubAgentSpawner(config)
        result = await spawner.spawn(
            command=[sys.executable, script],
            task="workspace test",
        )
        assert result.status == "success"
        assert result.data["workspace_exists"] is True
