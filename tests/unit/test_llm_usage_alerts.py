import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from simpleclaw.llm.usage import BackendPricing, LLMUsageEvent, NormalizedUsage
from simpleclaw.logging.llm_usage import LLMUsageService, LLMUsageStore


def _event(event_id):
    return LLMUsageEvent(event_id, datetime.now(UTC).isoformat(), "", "primary", "profile", "model", "default", "chat", "primary", None, "success", 1, NormalizedUsage(20_000, 0))


@pytest.mark.asyncio
async def test_daily_alert_claim_deduplicates_across_service_restart(tmp_path):
    sent = []
    async def callback(text):
        sent.append(text)
    pricing = {"primary": BackendPricing(version="v1", input_per_million_usd=Decimal(1), output_per_million_usd=Decimal(1))}
    path = tmp_path / "usage.db"
    LLMUsageService(LLMUsageStore(path), pricing=pricing, daily_usd=Decimal("0.01"), alert_callback=callback).record(_event("one"))
    await asyncio.sleep(0)
    LLMUsageService(LLMUsageStore(path), pricing=pricing, daily_usd=Decimal("0.01"), alert_callback=callback).record(_event("two"))
    await asyncio.sleep(0)
    assert len(sent) == 1
    assert "alert-only" in sent[0]
