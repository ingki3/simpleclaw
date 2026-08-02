import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from simpleclaw.llm.usage import LLMUsageEvent, NormalizedUsage
from simpleclaw.logging.llm_usage import LLMUsageStore, UsageThresholdClaim


def _event(event_id="one", *, cost=100, usage=None):
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
        1.0,
        usage or NormalizedUsage(10, 2),
        cost,
        "v1",
    )


def test_store_is_idempotent_and_survives_restart(tmp_path):
    path = tmp_path / "usage.db"
    store = LLMUsageStore(path)
    assert store.record(_event())
    assert not store.record(_event())
    summary = LLMUsageStore(path).summarize_day(
        datetime.now(UTC), timezone="Asia/Seoul"
    )
    assert summary["event_count"] == 1


def test_store_separates_unpriced_and_allowlists_grouping(tmp_path):
    store = LLMUsageStore(tmp_path / "usage.db")
    store.record(_event("priced"))
    store.record(_event("unpriced", cost=None))
    summary = store.summarize_month(
        datetime.now(UTC), timezone="Asia/Seoul", group_by="backend"
    )
    assert summary["priced_event_count"] == 1
    assert summary["unpriced_event_count"] == 1
    assert summary["unpriced_tokens"] == 12
    with pytest.raises(ValueError, match="group_by"):
        store.summarize_month(
            datetime.now(UTC), timezone="Asia/Seoul", group_by="backend; DROP"
        )


def test_threshold_claim_is_durable_and_deduplicated(tmp_path):
    path = tmp_path / "usage.db"
    claim = UsageThresholdClaim(
        "day", "2026-08-02", 100, datetime.now(UTC).isoformat(), 120
    )
    assert LLMUsageStore(path).claim_threshold(claim)
    assert not LLMUsageStore(path).claim_threshold(claim)


def test_store_redacts_every_unsafe_string_before_sqlite_boundary(tmp_path):
    path = tmp_path / "usage.db"
    marker = "sk-review-credential-marker"
    event = LLMUsageEvent(
        marker,
        datetime.now(UTC).isoformat(),
        "/private/user/path",
        marker,
        "credential-secret",
        "user supplied model",
        "../../route",
        "raw user task",
        "primary",
        "private reason",
        "success",
        1.0,
        NormalizedUsage(10, 2),
        100,
        "api_key=v1",
        "secret-error",
    )

    store = LLMUsageStore(path)
    assert store.record(event)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT * FROM llm_usage_events").fetchone()
    serialized = json.dumps(row)

    assert marker not in serialized
    assert "/private/user/path" not in serialized
    assert "raw user task" not in serialized
    assert "api_key=v1" not in serialized


def test_expired_pending_claim_is_reclaimed_once_after_restart(tmp_path):
    path = tmp_path / "usage.db"
    claim = UsageThresholdClaim(
        "day", "2026-08-02", 100, datetime.now(UTC).isoformat(), 120
    )
    now = datetime.now(UTC)

    assert LLMUsageStore(path).claim_threshold(
        claim, now_utc=now, lease_seconds=1, max_attempts=3
    )
    assert not LLMUsageStore(path).claim_threshold(
        claim, now_utc=now, lease_seconds=1, max_attempts=3
    )
    restarted = LLMUsageStore(path)
    assert restarted.claim_threshold(
        claim,
        now_utc=now + timedelta(seconds=2),
        lease_seconds=1,
        max_attempts=3,
    )
    assert restarted.get_alert_claim(claim)["attempt_count"] == 2


def test_reclaimed_claim_fences_stale_dispatch_completion(tmp_path):
    path = tmp_path / "usage.db"
    now = datetime.now(UTC)
    stale = UsageThresholdClaim("day", "2026-08-02", 100, now.isoformat(), 120)
    fresh_time = now + timedelta(seconds=2)
    fresh = UsageThresholdClaim("day", "2026-08-02", 100, fresh_time.isoformat(), 130)
    store = LLMUsageStore(path)

    assert store.claim_threshold(stale, now_utc=now, lease_seconds=1)
    assert store.claim_threshold(fresh, now_utc=fresh_time, lease_seconds=1)
    store.mark_alert_sent(stale)
    assert store.get_alert_claim(fresh)["dispatch_status"] == "pending"

    store.mark_alert_failed(
        fresh,
        "SyntheticError",
        retry_cooldown_seconds=0,
        now_utc=fresh_time,
    )
    assert store.get_alert_claim(fresh)["dispatch_status"] == "failed"
