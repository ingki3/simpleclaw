"""Integration tests for daemon lifecycle."""

import pytest

from simpleclaw.daemon.daemon import AgentDaemon
from simpleclaw.daemon.models import ActionType, DaemonLockError


@pytest.fixture
def config_file(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(f"""
daemon:
  heartbeat_interval: 1
  pid_file: "{tmp_path}/daemon.pid"
  status_file: "{tmp_path}/HEARTBEAT.md"
  db_path: "{tmp_path}/daemon.db"
  dreaming:
    overnight_hour: 3
    idle_threshold: 7200
  wait_state:
    default_timeout: 60
""")
    return config


class TestDaemonLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, config_file, tmp_path):
        daemon = AgentDaemon(config_file)
        await daemon.start()
        assert daemon.is_running()
        assert (tmp_path / "daemon.pid").exists()
        assert (tmp_path / "HEARTBEAT.md").exists()

        await daemon.stop()
        assert not daemon.is_running()
        assert not (tmp_path / "daemon.pid").exists()

    @pytest.mark.asyncio
    async def test_heartbeat_tick_runs(self, config_file):
        daemon = AgentDaemon(config_file)
        await daemon.start()
        tick = daemon.heartbeat.get_last_tick()
        assert tick is not None
        assert tick.timestamp is not None
        await daemon.stop()

    @pytest.mark.asyncio
    async def test_cron_job_crud(self, config_file):
        daemon = AgentDaemon(config_file)
        await daemon.start()

        cs = daemon.cron_scheduler
        job = cs.add_job("test", "0 9 * * *", ActionType.PROMPT, "Hello")
        assert job.name == "test"

        jobs = cs.list_jobs()
        assert len(jobs) == 1

        cs.update_job("test", cron_expression="30 8 * * *")
        updated = cs.get_job("test")
        assert updated.cron_expression == "30 8 * * *"

        cs.remove_job("test")
        assert cs.get_job("test") is None

        await daemon.stop()

    @pytest.mark.asyncio
    async def test_wait_state_manager(self, config_file):
        daemon = AgentDaemon(config_file)
        await daemon.start()

        wm = daemon.wait_state_manager
        assert wm is not None

        wm.register_wait("task-1", {"step": 1}, "callback", timeout=60)
        pending = wm.get_pending()
        assert len(pending) == 1

        wm.resolve_wait("task-1", "completed")
        pending = wm.get_pending()
        assert len(pending) == 0

        await daemon.stop()

    @pytest.mark.asyncio
    async def test_duplicate_instance_prevention(self, config_file, tmp_path):
        daemon1 = AgentDaemon(config_file)
        await daemon1.start()

        daemon2 = AgentDaemon(config_file)
        with pytest.raises(DaemonLockError):
            await daemon2.start()

        await daemon1.stop()
