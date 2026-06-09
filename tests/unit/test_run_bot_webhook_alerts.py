"""BIZ-34: run_bot webhook alert wiring helper tests."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


RUN_BOT_PATH = Path(__file__).parents[2] / "scripts" / "run_bot.py"


def _load_run_bot_module():
    """scripts/run_bot.py를 모듈로 로드한다.

    run_bot.py는 패키지 모듈이 아닌 launch script이므로 importlib spec로 직접 로드해
    helper 함수만 검증한다. ``if __name__ == "__main__"`` guard 덕분에 main은 실행되지 않는다.
    """
    spec = importlib.util.spec_from_file_location("run_bot_biz34", RUN_BOT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_select_webhook_alert_chat_id_prefers_chat_ids():
    run_bot = _load_run_bot_module()

    assert run_bot._select_webhook_alert_chat_id(
        {"chat_ids": [300], "user_ids": [100]}
    ) == 300


def test_select_webhook_alert_chat_id_falls_back_to_user_ids():
    run_bot = _load_run_bot_module()

    assert run_bot._select_webhook_alert_chat_id(
        {"chat_ids": [], "user_ids": [100]}
    ) == 100
    assert run_bot._select_webhook_alert_chat_id(
        {"chat_ids": [], "user_ids": []}
    ) is None


def test_format_webhook_alert_consecutive_blocks_korean_summary():
    run_bot = _load_run_bot_module()

    text = run_bot._format_webhook_alert(
        "consecutive_blocks",
        {"remote": "1.2.3.4", "count": 5, "threshold": 5},
    )

    assert "연속 차단" in text
    assert "1.2.3.4" in text
    assert "count: 5" in text
    assert "threshold: 5" in text


def test_format_webhook_alert_burst_and_queue_summary():
    run_bot = _load_run_bot_module()

    burst = run_bot._format_webhook_alert(
        "burst",
        {"remote": "1.2.3.4", "count": 100, "window_seconds": 10.0},
    )
    queue = run_bot._format_webhook_alert("queue_saturated", {})

    assert "단일 IP 폭주" in burst
    assert "window_seconds: 10.0" in burst
    assert "처리 큐 포화" in queue


@pytest.mark.asyncio
async def test_webhook_alert_notifier_logs_telegram_dispatch_failure(monkeypatch):
    run_bot = _load_run_bot_module()
    calls: list[dict] = []

    class FakeStructuredLogger:
        def log(self, **kwargs):
            calls.append(kwargs)

    class FailingBot:
        def __init__(self, token: str) -> None:
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def send_message(self, chat_id: int, text: str) -> None:
            raise RuntimeError("telegram down")

    fake_telegram = types.SimpleNamespace(Bot=FailingBot)
    monkeypatch.setitem(sys.modules, "telegram", fake_telegram)

    notifier = run_bot._create_webhook_alert_notifier(
        "fake-token",
        123,
        FakeStructuredLogger(),
    )
    await notifier("consecutive_blocks", {"remote": "1.2.3.4", "count": 5})

    assert calls
    assert calls[0]["action_type"] == "webhook_alert_dispatch_failed"
    assert calls[0]["status"] == "failed"
    assert calls[0]["alert_type"] == "consecutive_blocks"
    assert calls[0]["remote"] == "1.2.3.4"
