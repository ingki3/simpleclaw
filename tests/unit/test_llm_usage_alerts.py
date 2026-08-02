import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from simpleclaw.llm.usage import BackendPricing, LLMUsageEvent, NormalizedUsage
from simpleclaw.logging.llm_usage import (
    LLMUsageService,
    LLMUsageStore,
    UsageThresholdClaim,
)


def _event(event_id):
    return LLMUsageEvent(
        event_id,
        datetime.now(UTC).isoformat(),
        "",
        "primary",
        "profile",
        "model",
        "default",
        "chat",
        "primary",
        None,
        "success",
        1,
        NormalizedUsage(20_000, 0),
    )


@pytest.mark.asyncio
async def test_daily_alert_claim_deduplicates_across_service_restart(tmp_path):
    sent = []

    async def callback(text):
        sent.append(text)

    pricing = {
        "primary": BackendPricing(
            version="v1",
            input_per_million_usd=Decimal(1),
            output_per_million_usd=Decimal(1),
        )
    }
    path = tmp_path / "usage.db"
    LLMUsageService(
        LLMUsageStore(path),
        pricing=pricing,
        daily_usd=Decimal("0.01"),
        alert_callback=callback,
    ).record(_event("one"))
    await asyncio.sleep(0)
    LLMUsageService(
        LLMUsageStore(path),
        pricing=pricing,
        daily_usd=Decimal("0.01"),
        alert_callback=callback,
    ).record(_event("two"))
    await asyncio.sleep(0)
    assert len(sent) == 1
    assert "alert-only" in sent[0]


@pytest.mark.asyncio
async def test_failed_alert_is_retried_after_cooldown_across_restart(tmp_path):
    attempts = []

    async def fail_once(text):
        attempts.append(text)
        raise OSError("synthetic outage")

    async def succeed(text):
        attempts.append(text)

    pricing = {
        "primary": BackendPricing(
            version="v1",
            input_per_million_usd=Decimal(1),
            output_per_million_usd=Decimal(1),
        )
    }
    path = tmp_path / "usage.db"
    first = LLMUsageService(
        LLMUsageStore(path),
        pricing=pricing,
        daily_usd=Decimal("0.01"),
        alert_callback=fail_once,
        alert_cooldown_seconds=0,
    )
    first.record(_event("one"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    restarted_store = LLMUsageStore(path)
    second = LLMUsageService(
        restarted_store,
        pricing=pricing,
        daily_usd=Decimal("0.01"),
        alert_callback=succeed,
        alert_cooldown_seconds=0,
    )
    second.record(_event("two"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    now = datetime.now(UTC)
    claim = UsageThresholdClaim(
        "day",
        restarted_store._window(now, "Asia/Seoul", "day")[2],
        10_000,
        now.isoformat(),
        40_000,
    )
    state = restarted_store.get_alert_claim(claim)
    assert len(attempts) == 2
    assert state["dispatch_status"] == "sent"
    assert state["attempt_count"] == 2


@pytest.mark.asyncio
async def test_failed_alert_retry_is_bounded(tmp_path):
    attempts = 0

    async def always_fail(_text):
        nonlocal attempts
        attempts += 1
        raise OSError("synthetic outage")

    pricing = {
        "primary": BackendPricing(
            version="v1",
            input_per_million_usd=Decimal(1),
            output_per_million_usd=Decimal(1),
        )
    }
    service = LLMUsageService(
        LLMUsageStore(tmp_path / "usage.db"),
        pricing=pricing,
        daily_usd=Decimal("0.01"),
        alert_callback=always_fail,
        alert_cooldown_seconds=0,
        alert_max_attempts=3,
    )
    for index in range(5):
        service.record(_event(str(index)))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert attempts == 3
