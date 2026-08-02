"""Content-free LLM usage and estimated-cost contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol


def _token(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_tokens: int = 0
    provider_reported_cost_microusd: int | None = None

    @property
    def usage_known(self) -> bool:
        return self.input_tokens is not None or self.output_tokens is not None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass(frozen=True)
class BackendPricing:
    version: str
    source_url: str | None = None
    effective_from: str | None = None
    input_per_million_usd: Decimal | None = None
    output_per_million_usd: Decimal | None = None
    cache_read_per_million_usd: Decimal | None = None
    cache_write_per_million_usd: Decimal | None = None


@dataclass(frozen=True)
class LLMUsageEvent:
    event_id: str
    occurred_at_utc: str
    trace_id: str
    backend_name: str
    provider_profile: str
    model: str
    route_name: str
    task_name: str
    attempt_role: str
    retry_reason: str | None
    status: str
    duration_ms: float
    usage: NormalizedUsage
    estimated_cost_microusd: int | None = None
    pricing_version: str | None = None
    error_type: str | None = None

    def with_cost(self, cost: int | None, version: str | None) -> LLMUsageEvent:
        return replace(self, estimated_cost_microusd=cost, pricing_version=version)


class LLMUsageSink(Protocol):
    def record(self, event: LLMUsageEvent) -> None: ...


def normalize_usage(raw: dict | None) -> NormalizedUsage:
    if not isinstance(raw, dict):
        return NormalizedUsage()
    provider_cost = _token(raw.get("provider_reported_cost_microusd"))
    return NormalizedUsage(
        input_tokens=_token(raw.get("input_tokens")),
        output_tokens=_token(raw.get("output_tokens")),
        cache_read_input_tokens=_token(raw.get("cache_read_input_tokens")) or 0,
        cache_write_input_tokens=_token(raw.get("cache_write_input_tokens")) or 0,
        reasoning_tokens=_token(raw.get("reasoning_tokens")) or 0,
        provider_reported_cost_microusd=provider_cost,
    )


def estimate_cost_microusd(
    usage: NormalizedUsage, pricing: BackendPricing | None
) -> int | None:
    if pricing is None or not usage.usage_known:
        return None
    input_total = usage.input_tokens or 0
    cache_read = min(usage.cache_read_input_tokens, input_total)
    cache_write = min(usage.cache_write_input_tokens, input_total - cache_read)
    uncached = input_total - cache_read - cache_write
    components = (
        (uncached, pricing.input_per_million_usd),
        (cache_read, pricing.cache_read_per_million_usd),
        (cache_write, pricing.cache_write_per_million_usd),
        (usage.output_tokens or 0, pricing.output_per_million_usd),
    )
    if any(tokens > 0 and rate is None for tokens, rate in components):
        return None
    total = sum(Decimal(tokens) * (rate or Decimal(0)) for tokens, rate in components)
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def microusd_to_decimal_usd(value: int) -> Decimal:
    return Decimal(value) / Decimal(1_000_000)
