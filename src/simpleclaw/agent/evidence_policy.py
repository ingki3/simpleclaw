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
from urllib.parse import urlparse

from simpleclaw.agent.action_result import looks_like_explicit_error_header

if TYPE_CHECKING:
    from simpleclaw.agent.planner_catalog import PlannerCatalog
    from simpleclaw.agent.turn_analysis import TurnAnalysis
    from simpleclaw.agent.turn_plan import UnifiedTurnPlan

_WEB_COLLECTOR_NAMES = frozenset({"web_search", "web_fetch"})
_DEFAULT_NON_WEB_COLLECTOR_NAMES = frozenset(
    {
        "asset_inventory",
        "browser_handoff",
        "config_inspect",
        "deploy_status",
        "file_read",
        "log_debug",
        "mcp_call",
        "runtime_status",
        "study_status",
        "verification_evidence",
    }
)
_NON_EVIDENCE_READ_TOOLS = frozenset({"clarify", "search_memory", "skill_docs"})
_URL_RE = re.compile(r"https?://[^\s)\],]+")
_QUOTED_ENTITY_RE = re.compile(r"""["'“”‘’]([^"'“”‘’]{2,})["'“”‘’]""")
_QUERY_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_EXPLICIT_EMPTY_MESSAGES = frozenset(
    {
        "no results",
        "not found",
        "검색 결과가 없습니다",
        "찾을 수 없습니다",
    }
)
_QUERY_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "about",
        "find",
        "search",
        "show",
        "please",
        "정보",
        "검색",
        "조회",
        "확인",
        "찾아줘",
        "알려줘",
        "보여줘",
        "등장인물",
        "오늘",
        "현재",
        "이번",
        "최신",
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

    SEARCHING = "searching"
    FOUND = "found"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    NOT_SEARCHED = "not_searched"
    UNUSABLE = "unusable"
    VERIFIED = "verified"


class EvidenceSourceType(str, Enum):
    """Collector family that produced the current state."""

    NONE = "none"
    STRUCTURED_REALTIME = "structured_realtime"
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"
    APPROVED_TOOL = "approved_tool"


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
        return self.status in {EvidenceStatus.FOUND, EvidenceStatus.VERIFIED}


@dataclass(frozen=True)
class EvidenceRequirement:
    """One turn's immutable evidence outcome contract."""

    required: bool
    query: str = ""
    domain: str = ""
    allowed_collectors: frozenset[str] = frozenset()
    freshness_required: bool = False
    origin: str = ""
    owner: str = "none"
    entities: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    reference_date: str = ""
    required_claims: tuple[str, ...] = ()
    collector_validators: tuple[tuple[str, str], ...] = ()

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
    allowed_collectors: frozenset[str] = _WEB_COLLECTOR_NAMES,
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
        owner="planner" if required else "none",
        collector_validators=tuple(
            (name, "sourced_text") for name in allowed_collectors
        ),
    )


def approved_collectors_from_plan(
    plan: UnifiedTurnPlan,
    *,
    catalog: PlannerCatalog | None = None,
) -> frozenset[str]:
    """Return plan-scoped collectors approved by immutable capability metadata."""

    allowed_tools = frozenset(plan.execution.allowed_tools)
    if catalog is None:
        # The plan still constrains execution. Keep the no-catalog adapter useful
        # for tests and rollback callers, but exclude read-only tools that cannot
        # establish current external evidence by contract.
        return frozenset(
            name
            for name in allowed_tools
            if name in _WEB_COLLECTOR_NAMES
            or name in _DEFAULT_NON_WEB_COLLECTOR_NAMES
        )

    runtime_assets = {
        (asset.asset_type, asset.name): asset
        for asset in catalog.assets
        if asset.runtime_visible and asset.declared
    }
    collectors: set[str] = set()
    for name in allowed_tools:
        if name in _WEB_COLLECTOR_NAMES:
            collectors.add(name)
            continue
        if name == "execute_skill":
            # The adapter itself is generic; only a selected read-only skill can
            # make it an evidence collector.
            continue
        if name in _NON_EVIDENCE_READ_TOOLS:
            continue
        asset = runtime_assets.get(("native_tool", name))
        if (
            asset is not None
            and asset.read_only
            and not asset.side_effects
            and not asset.requires_confirmation
        ):
            collectors.add(name)

    if "execute_skill" in allowed_tools:
        selected_assets = set(plan.execution.allowed_assets)
        if plan.execution.primary_asset is not None:
            selected_assets.add(plan.execution.primary_asset)
        if any(
            (
                (asset := runtime_assets.get((ref.asset_type, ref.name))) is not None
                and ref.asset_type == "skill"
                and asset.read_only
                and not asset.side_effects
                and not asset.requires_confirmation
            )
            for ref in selected_assets
        ):
            collectors.add("execute_skill")
    return frozenset(collectors)


def requirement_from_turn_plan(
    plan: UnifiedTurnPlan,
    *,
    catalog: PlannerCatalog | None = None,
) -> EvidenceRequirement:
    """Adapt Unified FactCheckPlan without introducing a duplicate plan field."""

    fact_check = plan.fact_check
    collectors = approved_collectors_from_plan(plan, catalog=catalog)
    validators = tuple(
        (
            name,
            (
                "sourced_text"
                if name in _WEB_COLLECTOR_NAMES or name == "execute_skill"
                else "bounded_text"
            ),
        )
        for name in sorted(collectors)
    )
    if fact_check.required and fact_check.owner.value == "asset":
        primary = plan.execution.primary_asset
        if primary is not None:
            asset_collector = f"asset:{primary.asset_type}:{primary.name}"
            collectors = frozenset((*collectors, asset_collector))
            validators = (*validators, (asset_collector, "sourced_text"))
    return EvidenceRequirement(
        required=fact_check.required,
        query=fact_check.search_query or plan.context.standalone_question,
        domain=fact_check.domain,
        allowed_collectors=collectors if fact_check.required else frozenset(),
        freshness_required=fact_check.freshness_required,
        origin="unified_fact_check_plan",
        owner=fact_check.owner.value,
        entities=tuple(entity.value for entity in fact_check.entities),
        intents=tuple(fact_check.intents),
        reference_date=fact_check.reference_date,
        required_claims=tuple(fact_check.required_claims),
        collector_validators=validators,
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


def _normalized_relevance_text(value: str) -> str:
    return " ".join(_QUERY_TOKEN_RE.findall(value.casefold()))


def _result_is_relevant(query: str, text: str) -> bool:
    """Conservatively require the bounded query's entity or subject in output."""

    normalized_query = _normalized_relevance_text(query)
    normalized_text = _normalized_relevance_text(text)
    if not normalized_query or not normalized_text:
        return False

    query_url = urlparse(query.strip())
    if query_url.scheme in {"http", "https"} and query_url.netloc:
        return any(
            urlparse(url).netloc.casefold() == query_url.netloc.casefold()
            for url in _URL_RE.findall(text)
        )

    quoted_entities = [
        _normalized_relevance_text(entity)
        for entity in _QUOTED_ENTITY_RE.findall(query)
    ]
    quoted_entities = [entity for entity in quoted_entities if entity]
    if quoted_entities:
        return all(entity in normalized_text for entity in quoted_entities)

    tokens = [
        token.casefold()
        for token in _QUERY_TOKEN_RE.findall(query)
        if len(token) >= 2 and token.casefold() not in _QUERY_STOPWORDS
    ]
    unique_tokens = set(tokens)
    required_matches = 1 if len(unique_tokens) == 1 else 2
    return (
        bool(unique_tokens)
        and sum(token in normalized_text for token in unique_tokens)
        >= required_matches
    )


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
    schema_failure = ""
    if parsed is None:
        schema_failure = "structured evidence schema failure: expected JSON object"
    lookup_status = str((parsed or {}).get("lookup_status") or "")
    facts = (parsed or {}).get("facts")
    if not schema_failure and lookup_status not in {
        EvidenceStatus.FOUND.value,
        EvidenceStatus.NOT_FOUND.value,
        EvidenceStatus.FAILED.value,
        "unsupported",
        EvidenceStatus.UNUSABLE.value,
    }:
        schema_failure = "structured evidence schema failure: invalid lookup_status"
    if not schema_failure and not isinstance(facts, list):
        schema_failure = "structured evidence schema failure: facts must be a list"
    if (
        not schema_failure
        and isinstance(facts, list)
        and any(not isinstance(fact, dict) for fact in facts)
    ):
        schema_failure = "structured evidence schema failure: fact must be an object"
    if not schema_failure and lookup_status == EvidenceStatus.FOUND.value:
        if not facts:
            schema_failure = "structured evidence schema failure: found requires facts"
        elif not all(
            str(
                fact.get("source_url")
                or fact.get("url")
                or fact.get("source")
                or ""
            ).strip()
            for fact in facts
        ):
            schema_failure = (
                "structured evidence schema failure: found fact requires source"
            )
        elif not as_of:
            schema_failure = (
                "structured evidence schema failure: found requires freshness metadata"
            )
    if schema_failure:
        return EvidenceState(
            required=requirement.required,
            attempted=True,
            status=EvidenceStatus.FAILED,
            source_type=EvidenceSourceType.STRUCTURED_REALTIME,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason=schema_failure,
            evidence_text=evidence_text,
            query=requirement.query,
            as_of=as_of,
        )
    if lookup_status == EvidenceStatus.NOT_FOUND.value:
        status = (
            EvidenceStatus.NOT_FOUND
            if (parsed or {}).get("authoritative_empty") is True
            else EvidenceStatus.UNUSABLE
        )
    elif lookup_status == EvidenceStatus.FAILED.value:
        status = EvidenceStatus.FAILED
    elif lookup_status in {"unsupported", EvidenceStatus.UNUSABLE.value}:
        status = EvidenceStatus.UNUSABLE
    elif lookup_status == EvidenceStatus.FOUND.value and usable:
        status = EvidenceStatus.FOUND
    elif lookup_status == EvidenceStatus.FOUND.value:
        status = EvidenceStatus.UNUSABLE
    else:
        status = EvidenceStatus.UNUSABLE

    reason = failure_reason
    if not reason and status is EvidenceStatus.FAILED:
        limitations = (parsed or {}).get("limitations")
        if isinstance(limitations, list) and limitations:
            reason = str(limitations[0])[:240]
        else:
            reason = "structured provider failed"
    if not reason and lookup_status == "unsupported":
        reason = "structured provider does not support this request"
    if (
        not reason
        and lookup_status == EvidenceStatus.NOT_FOUND.value
        and status is EvidenceStatus.UNUSABLE
    ):
        reason = "not_found lacked authoritative empty-result evidence"
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
    """Classify a current-turn observation from a plan-approved collector."""

    source_type = (
        EvidenceSourceType.WEB_SEARCH
        if tool_name == "web_search"
        else EvidenceSourceType.WEB_FETCH
        if tool_name == "web_fetch"
        else EvidenceSourceType.APPROVED_TOOL
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
    if tool_name not in requirement.allowed_collectors:
        return EvidenceState(
            status=EvidenceStatus.UNUSABLE,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="tool is not an approved evidence collector",
            **common,
        )
    if not text:
        return EvidenceState(
            status=EvidenceStatus.FAILED,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="collector returned an untyped empty output",
            **common,
        )
    if looks_like_explicit_error_header(text):
        return EvidenceState(
            status=EvidenceStatus.FAILED,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason=(text.splitlines()[0] if text else "collector failed")[:240],
            **common,
        )
    typed = _json_object(text)
    typed_status = str((typed or {}).get("lookup_status") or "")
    if typed_status == EvidenceStatus.NOT_FOUND.value:
        return EvidenceState(
            status=EvidenceStatus.NOT_FOUND,
            freshness=EvidenceFreshness.CURRENT_TURN,
            **common,
        )
    if typed_status == EvidenceStatus.FAILED.value:
        return EvidenceState(
            status=EvidenceStatus.FAILED,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="collector reported a typed failure",
            **common,
        )
    first_line = lowered.splitlines()[0] if lowered else ""
    explicit_empty = (
        (
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
    if any(marker in lowered for marker in _STALE_MARKERS):
        return EvidenceState(
            status=EvidenceStatus.UNUSABLE,
            freshness=EvidenceFreshness.STALE,
            failure_reason="collector returned stale or pre-event evidence",
            **common,
        )
    relevance_terms = tuple(
        term
        for term in (*requirement.entities, *requirement.required_claims)
        if str(term).strip()
    )
    if (
        not _result_is_relevant(requirement.query, text)
        or any(
            _normalized_relevance_text(str(term))
            not in _normalized_relevance_text(text)
            for term in relevance_terms
        )
    ):
        return EvidenceState(
            status=EvidenceStatus.UNUSABLE,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="collector result is not relevant to the evidence query",
            **common,
        )
    validators = dict(requirement.collector_validators)
    validator = validators.get(
        tool_name,
        "sourced_text" if tool_name in _WEB_COLLECTOR_NAMES else "bounded_text",
    )
    if validator == "sourced_text" and not _URL_RE.search(text):
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
