from decimal import Decimal

from simpleclaw.llm.usage import BackendPricing, estimate_cost_microusd, normalize_usage


def test_missing_usage_stays_unknown():
    usage = normalize_usage(None)
    assert not usage.usage_known
    assert usage.total_tokens == 0


def test_negative_and_boolean_tokens_are_unknown():
    usage = normalize_usage({"input_tokens": -1, "output_tokens": True})
    assert usage.input_tokens is None
    assert usage.output_tokens is None


def test_cost_does_not_double_count_cache_or_reasoning():
    usage = normalize_usage({"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 400, "cache_write_input_tokens": 100, "reasoning_tokens": 50})
    pricing = BackendPricing(version="v1", input_per_million_usd=Decimal("3"), output_per_million_usd=Decimal("15"), cache_read_per_million_usd=Decimal("0.3"), cache_write_per_million_usd=Decimal("3.75"))
    assert estimate_cost_microusd(usage, pricing) == 4995


def test_missing_rate_is_unpriced_not_zero():
    assert estimate_cost_microusd(normalize_usage({"input_tokens": 1}), BackendPricing(version="v1")) is None
