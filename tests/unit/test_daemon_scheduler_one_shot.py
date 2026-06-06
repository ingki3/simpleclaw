"""one-shot/temporary cron 실행 후 cleanup semantics를 검증한다."""

from __future__ import annotations

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.daemon.models import ActionType
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore


class Agent:
    async def process_cron_message(self, message):
        return f"ok: {message}"


@pytest.mark.asyncio
async def test_one_shot_job_is_disabled_after_success(tmp_path):
    store = DaemonStore(tmp_path / "daemon.db")
    scheduler = CronScheduler(store, AsyncIOScheduler(), agent_orchestrator=Agent())
    scheduler.add_job(
        "once",
        "0 9 * * *",
        ActionType.PROMPT,
        "알림",
        run_once=True,
        max_runs=1,
    )

    execution = await scheduler.execute_job("once")

    assert execution.status.value == "success"
    stored = store.get_job("once")
    assert stored.enabled is False
    assert stored.run_count == 1
    assert stored.run_once is True


def test_one_shot_columns_migrate_with_defaults(tmp_path):
    store = DaemonStore(tmp_path / "daemon.db")
    job = store.get_job("missing")
    assert job is None
    cols = store._conn.execute("PRAGMA table_info(cron_jobs)").fetchall()
    names = {row[1] for row in cols}
    assert {"run_once", "expires_at", "max_runs", "run_count"} <= names
