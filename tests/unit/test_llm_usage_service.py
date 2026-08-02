from datetime import UTC, datetime
from decimal import Decimal

from simpleclaw.llm.usage import BackendPricing, LLMUsageEvent, NormalizedUsage
from simpleclaw.logging.llm_usage import LLMUsageService, LLMUsageStore
from simpleclaw.logging.metrics import MetricsCollector
from simpleclaw.logging.structured_logger import StructuredLogger


def _event(event_id="one"):
    return LLMUsageEvent(event_id, datetime.now(UTC).isoformat(), "trace", "primary", "openrouter", "model", "default", "chat", "primary", None, "success", 2.0, NormalizedUsage(10, 2))


def test_service_prices_persists_logs_and_metrics_content_free(tmp_path):
    store = LLMUsageStore(tmp_path / "usage.db")
    metrics = MetricsCollector()
    structured = StructuredLogger(tmp_path / "logs")
    service = LLMUsageService(store, pricing={"primary": BackendPricing(version="v1", input_per_million_usd=Decimal(1), output_per_million_usd=Decimal(2))}, metrics=metrics, structured_logger=structured)
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
    assert store.summarize_day(datetime.now(UTC), timezone="Asia/Seoul")["unpriced_event_count"] == 1
    assert metrics.get_snapshot().llm_unpriced_events == 1


def test_store_failure_is_fail_open(tmp_path):
    class BrokenStore:
        def record(self, event):
            raise OSError("disk")
    metrics = MetricsCollector()
    LLMUsageService(BrokenStore(), metrics=metrics).record(_event())
    assert metrics.get_snapshot().llm_usage_record_failures == 1
