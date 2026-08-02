from datetime import UTC, datetime
from decimal import Decimal

from simpleclaw.llm.usage import (
    BackendPricing,
    LLMUsageEvent,
    NormalizedUsage,
    estimate_cost_microusd,
    normalize_usage,
    provider_cost_usd_to_microusd,
    sanitize_usage_dimension,
)


def test_missing_usage_stays_unknown():
    usage = normalize_usage(None)
    assert not usage.usage_known
    assert usage.total_tokens == 0


def test_negative_and_boolean_tokens_are_unknown():
    usage = normalize_usage({"input_tokens": -1, "output_tokens": True})
    assert usage.input_tokens is None
    assert usage.output_tokens is None


def test_cost_does_not_double_count_cache_or_reasoning():
    usage = normalize_usage(
        {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 400,
            "cache_write_input_tokens": 100,
            "reasoning_tokens": 50,
        }
    )
    pricing = BackendPricing(
        version="v1",
        input_per_million_usd=Decimal(3),
        output_per_million_usd=Decimal(15),
        cache_read_per_million_usd=Decimal("0.3"),
        cache_write_per_million_usd=Decimal("3.75"),
    )
    assert estimate_cost_microusd(usage, pricing) == 4995


def test_missing_rate_is_unpriced_not_zero():
    assert (
        estimate_cost_microusd(
            normalize_usage({"input_tokens": 1}), BackendPricing(version="v1")
        )
        is None
    )


def test_provider_reported_usd_cost_is_exact_and_fail_closed():
    assert provider_cost_usd_to_microusd("0.0001234") == 123
    assert provider_cost_usd_to_microusd(-1) is None
    assert provider_cost_usd_to_microusd("not-a-cost") is None


def test_usage_event_redacts_all_unsafe_free_string_fields_deterministically():
    marker = "sk-review-credential-marker"
    event = LLMUsageEvent(
        event_id=marker,
        occurred_at_utc=datetime.now(UTC).isoformat(),
        trace_id="/Users/reviewer/private",
        backend_name=marker,
        provider_profile="credential-secret",
        model="user supplied model",
        route_name="../../private/route",
        task_name="prompt text marker",
        attempt_role="malformed",
        retry_reason="private reason",
        status="malformed",
        duration_ms=1,
        usage=NormalizedUsage(1, 1),
        pricing_version="api_key=v1",
        error_type="secret-error",
    )

    first = event.sanitized()
    second = event.sanitized()

    assert first == second
    assert marker not in repr(first)
    assert "/Users/reviewer" not in repr(first)
    assert first.attempt_role == "primary"
    assert first.retry_reason is None
    assert first.status == "error"
    for field, value in (
        ("event-id", first.event_id),
        ("trace", first.trace_id),
        ("backend", first.backend_name),
        ("provider-profile", first.provider_profile),
        ("model", first.model),
        ("route", first.route_name),
        ("task", first.task_name),
        ("pricing-version", first.pricing_version),
        ("error-type", first.error_type),
    ):
        assert value is not None and value.startswith(f"{field}-")


def test_benign_and_credential_shaped_values_are_not_trusted_by_character_shape():
    markers = (
        "private-user-message-marker-7f3a",
        "AKIAFAKESYNTHETIC1234",
        "ghp_FAKE_SYNTHETIC_MARKER_1234567890",
        "xoxbFAKESYNTHETIC1234567890",
    )

    for field in (
        "event_id",
        "trace_id",
        "backend_name",
        "provider_profile",
        "model",
        "route_name",
        "task_name",
        "pricing_version",
        "error_type",
    ):
        for marker in markers:
            sanitized = sanitize_usage_dimension(marker, field=field)
            assert sanitized != marker
            assert marker not in sanitized
