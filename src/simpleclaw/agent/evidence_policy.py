"""Provider-neutral current-turn evidence policy.

The policy deliberately accepts evidence only from an explicitly executed
current-turn collector. Conversation history, assistant messages, and generic
RAG text never enter this module and therefore cannot satisfy the contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from simpleclaw.agent.action_result import looks_like_explicit_error_header

if TYPE_CHECKING:
    from simpleclaw.agent.turn_analysis import TurnAnalysis
    from simpleclaw.agent.turn_plan import UnifiedTurnPlan

_COLLECTOR_NAMES = frozenset({"web_search", "web_fetch"})
_URL_RE = re.compile(r"https?://[^\s)\],]+")
_EXPLICIT_EMPTY_MESSAGES = frozenset(
    {
        "no results",
        "not found",
        "검색 결과가 없습니다",
        "찾을 수 없습니다",
    }
)
_STALE_MARKERS = (
    "stale_or_pre_event",
    '"status":"stale"',
    '"status": "stale"',
    "only stale",
)
_MAX_EVIDENCE_CHARS = 16_000


class EvidenceStatus(str, Enum):
    """Outcome of the required current-turn evidence attempt."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    NOT_SEARCHED = "not_searched"
    UNUSABLE = "unusable"


class EvidenceSourceType(str, Enum):
    """Collector family that produced the current state."""

    NONE = "none"
    STRUCTURED_REALTIME = "structured_realtime"
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"


class EvidenceFreshness(str, Enum):
    """Freshness assessment relevant to finalization."""

    CURRENT_TURN = "current_turn"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceState:
    """Structured evidence state passed into the finalization gate."""

    required: bool
    attempted: bool
    status: EvidenceStatus
    source_type: EvidenceSourceType = EvidenceSourceType.NONE
    freshness: EvidenceFreshness = EvidenceFreshness.UNKNOWN
    failure_reason: str = ""
    evidence_text: str = ""
    query: str = ""
    as_of: str = ""

    @property
    def usable(self) -> bool:
        return self.status is EvidenceStatus.FOUND


@dataclass(frozen=True)
class EvidenceRequirement:
    """One turn's immutable evidence outcome contract."""

    required: bool
    query: str = ""
    domain: str = ""
    allowed_collectors: frozenset[str] = frozenset()
    freshness_required: bool = False
    origin: str = ""

    def initial_state(self) -> EvidenceState:
        return EvidenceState(
            required=self.required,
            attempted=False,
            status=EvidenceStatus.NOT_SEARCHED,
            query=self.query,
        )


def no_evidence_requirement() -> EvidenceRequirement:
    """Return the ordinary-conversation contract."""

    return EvidenceRequirement(required=False)


def requirement_from_turn_analysis(
    analysis: TurnAnalysis,
    *,
    allowed_collectors: frozenset[str] = _COLLECTOR_NAMES,
) -> EvidenceRequirement:
    """Adapt the rollback-window TurnAnalysis schema to the common contract."""

    required = bool(
        getattr(analysis, "evidence_required", False)
        or getattr(analysis, "needs_current_facts", False)
    )
    domains = getattr(analysis, "domains", ())
    return EvidenceRequirement(
        required=required,
        query=str(getattr(analysis, "normalized_question", "") or ""),
        domain=str(domains[0]) if domains else "general",
        allowed_collectors=allowed_collectors if required else frozenset(),
        freshness_required=bool(getattr(analysis, "needs_current_facts", False)),
        origin="legacy_turn_analysis",
    )


def requirement_from_turn_plan(plan: UnifiedTurnPlan) -> EvidenceRequirement:
    """Adapt Unified FactCheckPlan without introducing a duplicate plan field."""

    fact_check = plan.fact_check
    collectors = frozenset(
        name for name in plan.execution.allowed_tools if name in _COLLECTOR_NAMES
    )
    return EvidenceRequirement(
        required=fact_check.required,
        query=fact_check.search_query or plan.context.standalone_question,
        domain=fact_check.domain,
        allowed_collectors=collectors if fact_check.required else frozenset(),
        freshness_required=fact_check.freshness_required,
        origin="unified_fact_check_plan",
    )


def _json_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def assess_realtime_result(
    requirement: EvidenceRequirement,
    result: object,
    *,
    usable: bool,
    as_of: str = "",
    failure_reason: str = "",
) -> EvidenceState:
    """Classify a typed realtime provider response without status coercion."""

    parsed = _json_object(result)
    evidence_text = (
        json.dumps(parsed, ensure_ascii=False)
        if parsed is not None
        else str(result or "")
    )[:_MAX_EVIDENCE_CHARS]
    lookup_status = str((parsed or {}).get("lookup_status") or "")
    if lookup_status == EvidenceStatus.NOT_FOUND.value:
        status = EvidenceStatus.NOT_FOUND
    elif lookup_status == EvidenceStatus.FAILED.value:
        status = EvidenceStatus.FAILED
    elif lookup_status == EvidenceStatus.FOUND.value and usable:
        status = EvidenceStatus.FOUND
    elif lookup_status == EvidenceStatus.FOUND.value:
        status = EvidenceStatus.UNUSABLE
    elif usable:
        # Rollback-window realtime fixtures predate the typed lookup_status
        # field. The domain validator's positive verdict remains authoritative.
        status = EvidenceStatus.FOUND
    else:
        status = EvidenceStatus.UNUSABLE

    reason = failure_reason
    if not reason and status is EvidenceStatus.FAILED:
        limitations = (parsed or {}).get("limitations")
        if isinstance(limitations, list) and limitations:
            reason = str(limitations[0])[:240]
        else:
            reason = "structured provider failed"
    return EvidenceState(
        required=requirement.required,
        attempted=True,
        status=status,
        source_type=EvidenceSourceType.STRUCTURED_REALTIME,
        freshness=(
            EvidenceFreshness.CURRENT_TURN
            if status is EvidenceStatus.FOUND
            else EvidenceFreshness.UNKNOWN
        ),
        failure_reason=reason,
        evidence_text=evidence_text,
        query=requirement.query,
        as_of=as_of,
    )


def assess_tool_result(
    requirement: EvidenceRequirement,
    *,
    tool_name: str,
    output: str,
    as_of: str = "",
) -> EvidenceState:
    """Classify a current-turn web collector observation."""

    source_type = (
        EvidenceSourceType.WEB_SEARCH
        if tool_name == "web_search"
        else EvidenceSourceType.WEB_FETCH
        if tool_name == "web_fetch"
        else EvidenceSourceType.NONE
    )
    text = str(output or "").strip()
    lowered = text.lower()
    common = {
        "required": requirement.required,
        "attempted": True,
        "source_type": source_type,
        "evidence_text": text[:_MAX_EVIDENCE_CHARS],
        "query": requirement.query,
        "as_of": as_of,
    }
    if tool_name not in _COLLECTOR_NAMES:
        return EvidenceState(
            status=EvidenceStatus.UNUSABLE,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="tool is not an approved evidence collector",
            **common,
        )
    if looks_like_explicit_error_header(text):
        return EvidenceState(
            status=EvidenceStatus.FAILED,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason=(text.splitlines()[0] if text else "collector failed")[:240],
            **common,
        )
    first_line = lowered.splitlines()[0] if lowered else ""
    explicit_empty = (
        not text
        or (
            tool_name == "web_search"
            and (
                "(0 results)" in first_line
                or first_line.endswith(": 0 results")
            )
        )
        or lowered in _EXPLICIT_EMPTY_MESSAGES
    )
    if explicit_empty:
        return EvidenceState(
            status=EvidenceStatus.NOT_FOUND,
            freshness=EvidenceFreshness.CURRENT_TURN,
            **common,
        )
    if requirement.freshness_required and any(
        marker in lowered for marker in _STALE_MARKERS
    ):
        return EvidenceState(
            status=EvidenceStatus.UNUSABLE,
            freshness=EvidenceFreshness.STALE,
            failure_reason="collector returned stale or pre-event evidence",
            **common,
        )
    if not _URL_RE.search(text):
        return EvidenceState(
            status=EvidenceStatus.UNUSABLE,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="collector result has no verifiable source URL",
            **common,
        )
    return EvidenceState(
        status=EvidenceStatus.FOUND,
        freshness=EvidenceFreshness.CURRENT_TURN,
        **common,
    )


def format_evidence_context(state: EvidenceState) -> str:
    """Render validated current-turn evidence for the final LLM request."""

    return "\n".join(
        (
            "## Validated Current-Turn Evidence",
            "Use only this evidence for externally verifiable factual claims.",
            "Do not claim that a search or lookup happened beyond the recorded source.",
            f"status: {state.status.value}",
            f"source_type: {state.source_type.value}",
            f"freshness: {state.freshness.value}",
            f"query: {state.query}",
            f"as_of: {state.as_of or 'current turn'}",
            state.evidence_text,
        )
    )


def limited_fallback(state: EvidenceState) -> str:
    """Return a deterministic, claim-limited response for unsatisfied evidence."""

    source = state.source_type.value
    query = state.query or "요청한 대상"
    as_of = state.as_of or "현재 turn"
    if state.status is EvidenceStatus.NOT_FOUND:
        return (
            f"조회는 완료했지만 `{query}`에 대한 확인 가능한 결과를 찾지 못했습니다. "
            f"(source: {source}, as-of: {as_of})"
        )
    if state.status is EvidenceStatus.FAILED:
        return (
            f"`{query}` 확인을 시도했지만 조회가 실패해 사실을 확정할 수 없습니다. "
            f"(source: {source}, as-of: {as_of})"
        )
    if state.status is EvidenceStatus.UNUSABLE:
        return (
            f"`{query}` 조회 결과는 있었지만 출처·관련성·신선도 조건을 충족하지 "
            f"못해 사실을 확정할 수 없습니다. (source: {source}, as-of: {as_of})"
        )
    return (
        f"`{query}`를 확인할 조회 도구를 사용할 수 없어 현재 사실을 확정할 수 "
        "없습니다. 조회를 수행했다고 간주하지 않았습니다."
    )
