import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from simpleclaw.llm.usage import BackendPricing, LLMUsageEvent, NormalizedUsage
from simpleclaw.logging.llm_usage import LLMUsageService, LLMUsageStore
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger


def _event(event_id="one"):
    return LLMUsageEvent(
        event_id,
        datetime.now(UTC).isoformat(),
        "trace",
        "primary",
        "openrouter",
        "model",
        "default",
        "chat",
        "primary",
        None,
        "success",
        2.0,
        NormalizedUsage(10, 2),
    )


def test_service_prices_persists_logs_and_metrics_content_free(tmp_path):
    store = LLMUsageStore(tmp_path / "usage.db")
    metrics = MetricsCollector()
    structured = StructuredLogger(tmp_path / "logs")
    service = LLMUsageService(
        store,
        pricing={
            "primary": BackendPricing(
                version="v1",
                input_per_million_usd=Decimal(1),
                output_per_million_usd=Decimal(2),
            )
        },
        metrics=metrics,
        structured_logger=structured,
    )
    service.record(_event())
    summary = store.summarize_day(datetime.now(UTC), timezone="Asia/Seoul")
    assert summary["estimated_cost_microusd"] == 14
    assert metrics.get_snapshot().llm_calls == 1
    entry = structured.get_entries()[-1]
    assert entry.input_summary == entry.output_summary == ""
    serialized = entry.to_json()
    assert "prompt" not in serialized.lower()
    assert "response" not in serialized.lower()


def test_unknown_price_is_unpriced_not_zero(tmp_path):
    store = LLMUsageStore(tmp_path / "usage.db")
    metrics = MetricsCollector()
    LLMUsageService(store, metrics=metrics).record(_event())
    assert (
        store.summarize_day(datetime.now(UTC), timezone="Asia/Seoul")[
            "unpriced_event_count"
        ]
        == 1
    )
    assert metrics.get_snapshot().llm_unpriced_events == 1


def test_store_failure_is_fail_open(tmp_path):
    class BrokenStore:
        def record(self, event):
            raise OSError("disk")

    metrics = MetricsCollector()
    LLMUsageService(BrokenStore(), metrics=metrics).record(_event())
    assert metrics.get_snapshot().llm_usage_record_failures == 1


@pytest.mark.asyncio
async def test_service_redacts_benign_markers_from_db_log_and_alert(tmp_path):
    markers = (
        "private-user-message-marker-7f3a",
        "AKIAFAKESYNTHETIC1234",
        "ghp_FAKE_SYNTHETIC_MARKER_1234567890",
        "xoxbFAKESYNTHETIC1234567890",
    )
    store = LLMUsageStore(tmp_path / "usage.db")
    structured = StructuredLogger(tmp_path / "logs")
    alerts = []

    async def alert_callback(text):
        alerts.append(text)

    event = LLMUsageEvent(
        markers[0],
        datetime.now(UTC).isoformat(),
        markers[1],
        markers[2],
        markers[3],
        markers[0],
        markers[1],
        markers[2],
        markers[3],
        markers[0],
        markers[1],
        1,
        NormalizedUsage(20_000, 1),
        pricing_version=markers[2],
        error_type=markers[3],
    )

    LLMUsageService(
        store,
        pricing={
            markers[2]: BackendPricing(
                version=markers[2],
                input_per_million_usd=Decimal(1),
                output_per_million_usd=Decimal(1),
            )
        },
        structured_logger=structured,
        daily_usd=Decimal("0.01"),
        alert_callback=alert_callback,
    ).record(event)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    log_json = structured.get_entries()[-1].to_json()
    api_payload = str(
        store.summarize_day(
            datetime.now(UTC), timezone="Asia/Seoul", group_by="backend"
        )
    )
    with sqlite3.connect(store.db_path) as conn:
        raw_db = json.dumps(conn.execute("SELECT * FROM llm_usage_events").fetchone())
    serialized_outputs = (raw_db, log_json, api_payload, "\n".join(alerts))
    assert alerts
    for marker in markers:
        assert all(marker not in output for output in serialized_outputs)
