"""LangGraph V4 Cron ingress, retry ownership, notifier 격리 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.daemon.daemon import AgentDaemon
from simpleclaw.daemon.models import ActionType, CronJob, ExecutionStatus
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore
from simpleclaw.graph_runtime.adapters.cron import (
    CronGraphResultV1,
    CronIngressAdapter,
)
from simpleclaw.graph_runtime.checkpoint import (
    CheckpointPathIsolationError,
    resolve_checkpoint_path,
)
from simpleclaw.graph_runtime.status import (
    DeliveryStatus,
    EffectStatus,
    TerminalOutcome,
)


class FakeCronFacade:
    def __init__(self, result: CronGraphResultV1) -> None:
        self.result = result
        self.ingresses = []

    async def execute_cron(self, ingress):
        self.ingresses.append(ingress)
        return self.result


def _scheduler(tmp_path, facade, notifier=None):
    store = DaemonStore(tmp_path / "daemon.db")
    scheduler = CronScheduler(
        store,
        AsyncIOScheduler(),
        graph_runtime_facade=facade,
        notifier=notifier,
    )
    return scheduler, store


def _job(
    *,
    action_type: ActionType = ActionType.PROMPT,
    action_reference: str = "매일 아침 요약",
) -> CronJob:
    return CronJob(
        name="morning",
        cron_expression="0 8 * * *",
        action_type=action_type,
        action_reference=action_reference,
        max_attempts=3,
        backoff_seconds=0,
    )


@pytest.mark.asyncio
async def test_daemon_injects_opt_in_v4_facade_without_db_migration(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "daemon:\n"
        "  heartbeat_interval: 60\n"
        f'  pid_file: "{tmp_path}/daemon.pid"\n'
        f'  status_file: "{tmp_path}/HEARTBEAT.md"\n'
        f'  db_path: "{tmp_path}/daemon.db"\n'
    )
    facade = FakeCronFacade(
        CronGraphResultV1(
            content="",
            terminal_outcome=TerminalOutcome.COMPLETED,
            delivery_status=DeliveryStatus.SUPPRESSED,
        )
    )
    daemon = AgentDaemon(config, graph_runtime_facade=facade)

    await daemon.start()
    try:
        assert daemon.cron_scheduler._graph_runtime_facade is facade
        assert daemon._db_path.name == "daemon.db"
    finally:
        await daemon.stop()


def test_prompt_and_recipe_ingress_preserve_cron_identity() -> None:
    adapter = CronIngressAdapter()

    prompt = adapter.normalize(_job(), run_id="run-prompt")
    recipe = adapter.normalize(
        _job(action_type=ActionType.RECIPE, action_reference="daily-report"),
        run_id="run-recipe",
    )

    assert prompt.envelope.request_id == "cron:run-prompt"
    assert prompt.checkpoint_thread_id == "cron:run-prompt"
    assert prompt.envelope.session_key == "cron:morning"
    assert prompt.envelope.original_text == "매일 아침 요약"
    assert prompt.envelope.cron is not None
    assert prompt.envelope.cron.job_id == "morning"
    assert prompt.envelope.cron.run_id == "run-prompt"
    assert prompt.preselected_recipe_ref is None
    assert recipe.envelope.original_text == "daily-report"
    assert recipe.preselected_recipe_ref == "daily-report"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        TerminalOutcome.BLOCKED,
        TerminalOutcome.UNSUPPORTED,
        TerminalOutcome.FAILED,
        TerminalOutcome.CANCELLED,
        TerminalOutcome.TIMED_OUT,
    ],
)
async def test_v4_solver_result_is_never_retried_by_scheduler(
    tmp_path, outcome
) -> None:
    facade = FakeCronFacade(
        CronGraphResultV1(
            content="실행하지 않았습니다.",
            terminal_outcome=outcome,
            delivery_status=DeliveryStatus.SUPPRESSED,
        )
    )
    notifications = []

    async def notify(job_name, text):
        notifications.append((job_name, text))

    scheduler, store = _scheduler(tmp_path, facade, notify)
    job = _job()
    store.save_job(job)

    execution = await scheduler.execute_job(job.name)

    assert execution.status is ExecutionStatus.FAILURE
    assert execution.attempt == 1
    assert len(facade.ingresses) == 1
    assert notifications == []
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery_status",
    [
        DeliveryStatus.SUPPRESSED,
        DeliveryStatus.SHADOWED,
        DeliveryStatus.FAILED_BEFORE_SEND,
        DeliveryStatus.UNKNOWN,
        DeliveryStatus.DELIVERED,
        DeliveryStatus.DISPATCHING,
    ],
)
async def test_non_ready_graph_delivery_never_calls_live_notifier(
    tmp_path, delivery_status
) -> None:
    facade = FakeCronFacade(
        CronGraphResultV1(
            content="완료 결과",
            terminal_outcome=TerminalOutcome.COMPLETED,
            delivery_status=delivery_status,
            effect_status=(
                EffectStatus.UNKNOWN
                if delivery_status is DeliveryStatus.UNKNOWN
                else EffectStatus.NONE
            ),
        )
    )
    notifications = []

    async def notify(job_name, text):
        notifications.append((job_name, text))

    scheduler, store = _scheduler(tmp_path, facade, notify)
    job = _job()
    store.save_job(job)

    execution = await scheduler.execute_job(job.name)

    # resolution과 delivery는 별도 축이므로 graph 해결 성공은 유지한다.
    assert execution.status is ExecutionStatus.SUCCESS
    assert notifications == []
    store.close()


@pytest.mark.asyncio
async def test_ready_graph_result_uses_existing_notifier_exactly_once(tmp_path) -> None:
    facade = FakeCronFacade(
        CronGraphResultV1(
            content="완료 결과",
            terminal_outcome=TerminalOutcome.COMPLETED,
            delivery_status=DeliveryStatus.READY,
        )
    )
    notifications = []

    async def notify(job_name, text):
        notifications.append((job_name, text))

    scheduler, store = _scheduler(tmp_path, facade, notify)
    job = _job(action_type=ActionType.RECIPE, action_reference="daily-report")
    store.save_job(job)

    execution = await scheduler.execute_job(job.name)

    assert execution.status is ExecutionStatus.SUCCESS
    assert notifications == [("morning", "완료 결과")]
    assert len(facade.ingresses) == 1
    assert facade.ingresses[0].preselected_recipe_ref == "daily-report"
    store.close()


@pytest.mark.asyncio
async def test_empty_graph_output_is_suppressed(tmp_path) -> None:
    facade = FakeCronFacade(
        CronGraphResultV1(
            content="",
            terminal_outcome=TerminalOutcome.COMPLETED,
            delivery_status=DeliveryStatus.READY,
        )
    )
    notifications = []

    async def notify(job_name, text):
        notifications.append((job_name, text))

    scheduler, store = _scheduler(tmp_path, facade, notify)
    job = _job()
    store.save_job(job)

    await scheduler.execute_job(job.name)

    assert notifications == []
    store.close()


def test_graph_checkpoint_path_is_separate_from_operational_databases(
    tmp_path,
) -> None:
    checkpoint = tmp_path / "graph-checkpoints.sqlite3"
    daemon_db = tmp_path / "daemon.db"
    conversations_db = tmp_path / "conversations.db"

    assert resolve_checkpoint_path(
        checkpoint,
        daemon_db_path=daemon_db,
        conversations_db_path=conversations_db,
    ) == checkpoint.resolve()
    for shared_path in (daemon_db, conversations_db):
        with pytest.raises(CheckpointPathIsolationError):
            resolve_checkpoint_path(
                shared_path,
                daemon_db_path=daemon_db,
                conversations_db_path=conversations_db,
            )


def test_default_graph_checkpoint_filename_is_not_daemon_or_conversation_db() -> None:
    path = resolve_checkpoint_path()
    assert isinstance(path, Path)
    assert path.name == "graph-checkpoints.sqlite3"
    assert path.name not in {"daemon.db", "conversations.db"}
