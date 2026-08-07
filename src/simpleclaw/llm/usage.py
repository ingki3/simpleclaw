"""내용을 저장하지 않는 LLM 사용량·예상 비용 계약을 제공한다.

provider별 원시 usage를 공통 토큰 구조로 정규화하고, 저장 가능한 이벤트의
문자열 dimension을 제한해 사용자 원문·경로·credential이 회계 경계로 넘어가지
않도록 한다. 비용 계산은 부동소수점 오차를 피하기 위해 micro-USD 정수로 수행한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

_ATTEMPT_ROLES = {"primary", "retry"}
_RETRY_REASONS = {"provider_error", "empty_final", "validation_error"}
_STATUSES = {"success", "empty", "error"}
USAGE_TASK_NAMES = frozenset(
    {
        "asset_selector",
        "chat",
        "dreaming_memory",
        "dreaming_profile",
        "dreaming_summary",
        "fact_answer",
        "goal_judge",
        "langgraph_v4_composer",
        "recipe_suggestion",
        "skill_suggestion",
        "tool_loop",
        "turn_analysis",
        "turn_planner",
    }
)
USAGE_ROUTE_NAMES = frozenset({"default", "direct", "multimodal", "turn_analysis"})


@dataclass(frozen=True)
class UsageDimensionRegistry:
    """원문 노출이 허용된 실제 구성·코드 dimension의 bounded registry다."""

    provider_profiles: frozenset[str] = frozenset()
    route_names: frozenset[str] = frozenset()
    task_names: frozenset[str] = USAGE_TASK_NAMES

    def values_for(self, field: str) -> frozenset[str]:
        """필드에 대응하는 닫힌 원문 허용 집합을 반환한다."""
        return {
            "provider_profile": self.provider_profiles,
            "route_name": self.route_names,
            "task_name": self.task_names,
        }.get(field, frozenset())


def _token(value: object) -> int | None:
    """음수가 아닌 실제 정수만 token/cost 값으로 허용한다."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def sanitize_usage_dimension(
    value: object,
    *,
    field: str,
    optional: bool = False,
    allowed_values: frozenset[str] | set[str] | None = None,
) -> str | None:
    """registry가 증명한 값만 원문으로 두고 나머지는 결정적으로 치환한다.

    문자 모양이나 credential denylist는 신뢰 근거로 사용하지 않는다. 명시적
    registry가 없는 자유 값은 예외 없이 field별 SHA-256 surrogate가 된다.
    """
    if optional and value is None:
        return None
    if (
        isinstance(value, str)
        and allowed_values is not None
        and value in allowed_values
    ):
        return value
    digest = hashlib.sha256(f"{field}\0{value!s}".encode()).hexdigest()[:16]
    prefix = field.removesuffix("_name").replace("_", "-")[:24]
    return f"{prefix}-{digest}"


def provider_cost_usd_to_microusd(value: object) -> int | None:
    """provider가 보고한 USD 비용을 검증해 micro-USD 정수로 변환한다."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001 - SDK의 임의 scalar 타입을 fail-closed 처리한다.
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int(
        (amount * Decimal(1_000_000)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


@dataclass(frozen=True)
class NormalizedUsage:
    """provider 중립 token breakdown과 선택적 provider 보고 비용이다."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_tokens: int = 0
    provider_reported_cost_microusd: int | None = None

    @property
    def usage_known(self) -> bool:
        """input/output 중 하나라도 provider가 보고했는지 반환한다."""
        return self.input_tokens is not None or self.output_tokens is not None

    @property
    def total_tokens(self) -> int:
        """알려진 input/output token 합계를 반환한다."""
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass(frozen=True)
class BackendPricing:
    """특정 backend 가격표의 버전·근거·token별 단가다."""

    version: str
    source_url: str | None = None
    effective_from: str | None = None
    input_per_million_usd: Decimal | None = None
    output_per_million_usd: Decimal | None = None
    cache_read_per_million_usd: Decimal | None = None
    cache_write_per_million_usd: Decimal | None = None


@dataclass(frozen=True)
class LLMUsageEvent:
    """실제 provider attempt 하나에 대응하는 content-free 회계 이벤트다."""

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
        """원본 이벤트를 유지하면서 계산 비용과 가격 버전을 연결한다."""
        return replace(self, estimated_cost_microusd=cost, pricing_version=version)

    def sanitized(
        self, registry: UsageDimensionRegistry | None = None
    ) -> LLMUsageEvent:
        """모든 문자열 필드를 저장 가능한 bounded 값으로 fail-closed 정규화한다."""
        registry = registry or UsageDimensionRegistry()
        try:
            occurred = datetime.fromisoformat(self.occurred_at_utc)
            if occurred.tzinfo is None:
                raise ValueError
            occurred_at_utc = occurred.astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            occurred_at_utc = datetime.now(UTC).isoformat()
        attempt_role = (
            self.attempt_role if self.attempt_role in _ATTEMPT_ROLES else "primary"
        )
        retry_reason = (
            self.retry_reason if self.retry_reason in _RETRY_REASONS else None
        )
        status = self.status if self.status in _STATUSES else "error"
        return replace(
            self,
            event_id=sanitize_usage_dimension(self.event_id, field="event_id")
            or "event-redacted",
            occurred_at_utc=occurred_at_utc,
            trace_id=sanitize_usage_dimension(self.trace_id, field="trace_id")
            or "redacted",
            backend_name=sanitize_usage_dimension(
                self.backend_name, field="backend_name"
            )
            or "backend-redacted",
            provider_profile=sanitize_usage_dimension(
                self.provider_profile,
                field="provider_profile",
                allowed_values=registry.values_for("provider_profile"),
            )
            or "provider-profile-redacted",
            model=sanitize_usage_dimension(self.model, field="model")
            or "model-redacted",
            route_name=sanitize_usage_dimension(
                self.route_name,
                field="route_name",
                allowed_values=registry.values_for("route_name"),
            )
            or "route-redacted",
            task_name=sanitize_usage_dimension(
                self.task_name,
                field="task_name",
                allowed_values=registry.values_for("task_name"),
            )
            or "task-redacted",
            attempt_role=attempt_role,
            retry_reason=retry_reason,
            status=status,
            pricing_version=sanitize_usage_dimension(
                self.pricing_version, field="pricing_version", optional=True
            ),
            error_type=sanitize_usage_dimension(
                self.error_type, field="error_type", optional=True
            ),
        )


class LLMUsageSink(Protocol):
    """라우터가 회계 저장소 구현과 결합되지 않게 하는 최소 기록 계약이다."""

    def record(self, event: LLMUsageEvent) -> None:
        """attempt event 하나를 fail-open 회계 경계로 전달한다."""
        ...


def normalize_usage(raw: dict | None) -> NormalizedUsage:
    """provider 응답 dict를 공통 token breakdown으로 정규화한다."""
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
    """cache subset을 중복 과금하지 않고 예상 비용을 micro-USD로 계산한다."""
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
    """표시용 micro-USD 정수를 정확한 Decimal USD로 변환한다."""
    return Decimal(value) / Decimal(1_000_000)
