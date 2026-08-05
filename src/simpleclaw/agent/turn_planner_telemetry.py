"""Unified TurnPlanner shadow 관측을 위한 원문 비포함 telemetry.

이 모듈의 public event shape는 enum, stable code, count, latency/token 수치만
받는다. 사용자 원문·선택된 turn content·검색 query·사용자/채팅 ID를 인자로
받지 않으므로 호출자가 실수로 민감 payload를 직렬화할 경로를 만들지 않는다.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.plan_gate import PlanGateResult
from simpleclaw.agent.turn_plan import UnifiedTurnPlan
from simpleclaw.llm.models import LLMRequest, LLMResponse
from simpleclaw.llm.router import LLMRouter

logger = logging.getLogger(__name__)

_Validated = TypeVar("_Validated")
_EVENT_NAME = "unified_turn_plan_shadow"
_UNKNOWN = "unknown"


def _nonnegative_int(value: object) -> int:
    """bool/비정상 usage 값을 telemetry에 넣지 않고 0으로 정규화한다."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _percentile(values: list[int], quantile: float) -> float | None:
    """외부 evaluator에 의존하지 않는 deterministic 선형 보간 percentile."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True)
class TurnPlannerShadowEvent:
    """Structured logger와 acceptance aggregate가 공유하는 redacted event."""

    event: str
    ok: bool
    relation: str
    selected_turn_count: int
    execution_mode: str
    asset_count: int
    fact_required: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    gate_status: str
    violation_codes: tuple[str, ...]
    catalog_fingerprint: str
    candidate_turn_count: int
    candidate_context_chars: int
    selected_context_chars: int
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        """tuple만 JSON array로 바꿔 stable event mapping을 반환한다."""
        payload = asdict(self)
        payload["violation_codes"] = list(self.violation_codes)
        return payload

    def to_json(self) -> str:
        """한국어 원문을 받을 필드가 없는 compact JSON으로 직렬화한다."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class PlannerUsageCaptureRouter(LLMRouter):
    """LLMRouter의 validated retry 계약을 보존하며 응답 usage만 누적한다.

    ``plan_turn_with_llm``은 ``LLMRouter`` 인스턴스에만 ``send_validated``를
    사용하므로 이 얇은 adapter도 같은 타입으로 유지한다. parent initializer는
    호출하지 않고 모든 실제 routing을 기존 router에 위임한다.
    """

    def __init__(self, wrapped: LLMRouter) -> None:
        self._wrapped = wrapped
        self.response_count = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _capture(self, response: LLMResponse) -> None:
        self.response_count += 1
        usage = response.usage if isinstance(response.usage, Mapping) else {}
        self.input_tokens += _nonnegative_int(usage.get("input_tokens"))
        self.output_tokens += _nonnegative_int(usage.get("output_tokens"))

    async def send_validated(
        self,
        request: LLMRequest,
        validate_response: Callable[[LLMResponse], _Validated],
        *,
        validation_retry_request=None,
    ) -> _Validated:
        """Primary와 semantic retry 응답의 token usage를 모두 누적한다."""

        def capture_then_validate(response: LLMResponse) -> _Validated:
            self._capture(response)
            return validate_response(response)

        return await self._wrapped.send_validated(
            request,
            capture_then_validate,
            validation_retry_request=validation_retry_request,
        )


def build_turn_planner_shadow_event(
    *,
    plan: UnifiedTurnPlan,
    gate_result: PlanGateResult,
    candidates: ContextCandidateSet,
    latency_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> TurnPlannerShadowEvent:
    """성공한 planner/gate 결과에서 원문 없는 shadow event를 만든다."""
    by_id = {
        candidate.turn_id: candidate for candidate in candidates.candidates
    }
    selected_context_chars = sum(
        len(by_id[turn_id].content)
        for turn_id in plan.context.selected_turn_ids
        if turn_id in by_id
    )
    return TurnPlannerShadowEvent(
        event=_EVENT_NAME,
        ok=True,
        relation=plan.context.relation.value,
        selected_turn_count=len(plan.context.selected_turn_ids),
        execution_mode=plan.execution.mode.value,
        asset_count=len(plan.capability.supporting_assets),
        fact_required=plan.fact_check.required,
        latency_ms=_nonnegative_int(round(latency_ms)),
        input_tokens=_nonnegative_int(input_tokens),
        output_tokens=_nonnegative_int(output_tokens),
        gate_status=gate_result.status.value,
        violation_codes=tuple(
            sorted({violation.code for violation in gate_result.violations})
        ),
        catalog_fingerprint=plan.catalog_fingerprint[:128],
        candidate_turn_count=len(candidates.candidates),
        candidate_context_chars=_nonnegative_int(candidates.total_chars),
        selected_context_chars=selected_context_chars,
    )


def build_turn_planner_shadow_failure_event(
    *,
    candidates: ContextCandidateSet,
    catalog_fingerprint: str,
    latency_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    error_code: str = "planner_unavailable",
) -> TurnPlannerShadowEvent:
    """예외 본문/타입 대신 stable code만 보존하는 실패 event를 만든다."""
    return TurnPlannerShadowEvent(
        event=_EVENT_NAME,
        ok=False,
        relation=_UNKNOWN,
        selected_turn_count=0,
        execution_mode=_UNKNOWN,
        asset_count=0,
        fact_required=False,
        latency_ms=_nonnegative_int(round(latency_ms)),
        input_tokens=_nonnegative_int(input_tokens),
        output_tokens=_nonnegative_int(output_tokens),
        gate_status=_UNKNOWN,
        violation_codes=(),
        catalog_fingerprint=str(catalog_fingerprint)[:128],
        candidate_turn_count=len(candidates.candidates),
        candidate_context_chars=_nonnegative_int(candidates.total_chars),
        selected_context_chars=0,
        error_code=str(error_code)[:80] or "planner_unavailable",
    )


def emit_turn_planner_shadow_event(
    event: TurnPlannerShadowEvent,
    *,
    structured_logger: Any | None = None,
) -> None:
    """redacted event를 표준/구조화 로그에 남기며 로깅 실패를 격리한다."""
    logger.info("Unified TurnPlanner shadow telemetry: %s", event.to_json())
    if structured_logger is None:
        return
    payload = event.to_dict()
    payload.pop("event", None)
    payload.pop("latency_ms", None)
    try:
        structured_logger.log(
            action_type=event.event,
            duration_ms=event.latency_ms,
            status="success" if event.ok else "error",
            # Shadow aggregate는 ordinary turn trace와 의도적으로 분리한다.
            # create_task가 contextvars를 복사하므로 생략하면 원대화 trace를 상속한다.
            trace_id="",
            **payload,
        )
    except Exception:  # noqa: BLE001 — telemetry 실패는 응답/worker를 막지 않는다.
        logger.warning(
            "Unified TurnPlanner structured telemetry write failed "
            "(error_code=telemetry_write_failed)"
        )


def aggregate_turn_planner_shadow_events(
    events: Iterable[TurnPlannerShadowEvent],
) -> dict[str, object]:
    """shadow event를 acceptance 검토용 redacted summary로 집계한다."""
    samples = list(events)
    latencies = [event.latency_ms for event in samples]
    input_tokens = [event.input_tokens for event in samples]
    output_tokens = [event.output_tokens for event in samples]
    gate_counts = Counter(event.gate_status for event in samples)
    relation_counts = Counter(event.relation for event in samples)
    mode_counts = Counter(event.execution_mode for event in samples)
    violation_counts = Counter(
        code for event in samples for code in event.violation_codes
    )
    context_reductions = [
        1.0 - (event.selected_context_chars / event.candidate_context_chars)
        for event in samples
        if event.candidate_context_chars > 0
    ]
    sample_count = len(samples)
    return {
        "schema_version": "turn-planner-shadow.v1",
        "samples": sample_count,
        "ok": sum(event.ok for event in samples),
        "failed": sum(not event.ok for event in samples),
        "gate_status": dict(sorted(gate_counts.items())),
        "relations": dict(sorted(relation_counts.items())),
        "execution_modes": dict(sorted(mode_counts.items())),
        "violations": dict(sorted(violation_counts.items())),
        "latency_ms": {
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
        },
        "tokens": {
            "input_total": sum(input_tokens),
            "output_total": sum(output_tokens),
            "input_average": (
                sum(input_tokens) / sample_count if sample_count else 0.0
            ),
            "output_average": (
                sum(output_tokens) / sample_count if sample_count else 0.0
            ),
        },
        "context_reduction_rate": (
            sum(context_reductions) / len(context_reductions)
            if context_reductions
            else 0.0
        ),
    }
