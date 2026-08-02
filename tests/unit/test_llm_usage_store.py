from datetime import UTC, datetime

import pytest

from simpleclaw.llm.usage import LLMUsageEvent, NormalizedUsage
from simpleclaw.logging.llm_usage import LLMUsageStore, UsageThresholdClaim


def _event(event_id="one", *, cost=100, usage=None):
    return LLMUsageEvent(event_id, datetime.now(UTC).isoformat(), "trace", "primary", "openrouter", "model", "default", "chat", "primary", None, "success", 1.0, usage or NormalizedUsage(10, 2), cost, "v1")


def test_store_is_idempotent_and_survives_restart(tmp_path):
    path = tmp_path / "usage.db"
    store = LLMUsageStore(path)
    assert store.record(_event())
    assert not store.record(_event())
    summary = LLMUsageStore(path).summarize_day(datetime.now(UTC), timezone="Asia/Seoul")
    assert summary["event_count"] == 1


def test_store_separates_unpriced_and_allowlists_grouping(tmp_path):
    store = LLMUsageStore(tmp_path / "usage.db")
    store.record(_event("priced"))
    store.record(_event("unpriced", cost=None))
    summary = store.summarize_month(datetime.now(UTC), timezone="Asia/Seoul", group_by="backend")
    assert summary["priced_event_count"] == 1
    assert summary["unpriced_event_count"] == 1
    assert summary["unpriced_tokens"] == 12
    with pytest.raises(ValueError, match="group_by"):
        store.summarize_month(datetime.now(UTC), timezone="Asia/Seoul", group_by="backend; DROP")


def test_threshold_claim_is_durable_and_deduplicated(tmp_path):
    path = tmp_path / "usage.db"
    claim = UsageThresholdClaim("day", "2026-08-02", 100, datetime.now(UTC).isoformat(), 120)
    assert LLMUsageStore(path).claim_threshold(claim)
    assert not LLMUsageStore(path).claim_threshold(claim)
