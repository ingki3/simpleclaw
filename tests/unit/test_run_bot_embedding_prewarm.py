"""BIZ-519 embedding pre-warm 시작 순서와 fail-open 계약 테스트."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from simpleclaw.agent.orchestrator import AgentOrchestrator

RUN_BOT_PATH = Path(__file__).parents[2] / "scripts" / "run_bot.py"


def _orchestrator(*, service, structured_logger) -> AgentOrchestrator:
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._embedding_service = service
    orchestrator._rag_model_name = (
        service.model_name
        if service is not None
        else "intfloat/multilingual-e5-small"
    )
    orchestrator._structured_logger = structured_logger
    return orchestrator


@pytest.mark.asyncio
async def test_prewarm_runs_in_worker_and_records_redacted_success(monkeypatch):
    service = MagicMock(model_name="safe/model", is_enabled=True)
    service.prewarm.return_value = True
    structured_logger = MagicMock()
    calls: list[object] = []

    async def fake_to_thread(func):
        calls.append(func)
        return func()

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    orchestrator = _orchestrator(
        service=service,
        structured_logger=structured_logger,
    )

    assert await orchestrator.prewarm_embedding() is True

    assert calls == [service.prewarm]
    structured_logger.log.assert_called_once()
    event = structured_logger.log.call_args.kwargs
    assert event["action_type"] == "embedding_prewarm"
    assert event["status"] == "success"
    assert event["model"] == "safe/model"
    assert event["duration_ms"] >= 0
    assert event["raw_text_included"] is False


@pytest.mark.asyncio
async def test_disabled_prewarm_skips_worker_and_is_fail_open(monkeypatch):
    structured_logger = MagicMock()

    async def unexpected_to_thread(func):
        raise AssertionError(f"worker must not run: {func}")

    monkeypatch.setattr(asyncio, "to_thread", unexpected_to_thread)
    orchestrator = _orchestrator(
        service=None,
        structured_logger=structured_logger,
    )

    assert await orchestrator.prewarm_embedding() is False
    assert structured_logger.log.call_args.kwargs["status"] == "disabled"


@pytest.mark.asyncio
async def test_load_failure_is_fail_open_and_does_not_log_sensitive_exception():
    secret = "credential=top-secret raw conversation"
    service = MagicMock(model_name="safe/model", is_enabled=True)
    service.prewarm.side_effect = RuntimeError(secret)
    structured_logger = MagicMock()
    orchestrator = _orchestrator(
        service=service,
        structured_logger=structured_logger,
    )

    assert await orchestrator.prewarm_embedding() is False

    event = structured_logger.log.call_args.kwargs
    assert event["status"] == "failure"
    assert secret not in json.dumps(event)


def test_startup_awaits_prewarm_after_admin_before_scheduler_and_polling():
    source = RUN_BOT_PATH.read_text(encoding="utf-8")

    admin_start = source.index("await admin_api.start()")
    prewarm = source.index("await orchestrator.prewarm_embedding()")
    scheduler_start = source.index("apscheduler.start()")
    polling_start = source.index("await bot.start()")

    assert admin_start < prewarm < scheduler_start < polling_start
