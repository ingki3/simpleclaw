"""Unified TurnPlanner의 불변 계획 모델과 provider-neutral JSON schema.

이 모듈은 LLM이 한 번에 반환한 문맥 선택·clarification·사실 확인·실행 범위를
Python 불변 모델로 변환한다. ``execution.mode``만 상위 실행 분기의 source of
truth로 유지하고, 기존 ``RouteDecision``은 production 전환기 호환 adapter로만
파생한다.

설계 결정:
- provider schema에는 nullable object 대신 명시적 no-asset sentinel을 둔다.
- parser는 enum·문자열·배열과 안전한 cross-field 불변식만 결정적으로 보정한다.
  사용자 텍스트 키워드로 route나 capability를 다시 추론하지 않는다.
- evaluator fixture의 기존 compact prediction shape도 읽을 수 있지만, 실제
  structured request schema는 모든 필드를 required로 강제한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

from simpleclaw.agent.resolution_types import (
    CapabilityCoverage,
    ComplexitySignal,
    ExecutionMode,
)
from simpleclaw.agent.response_router import ResponseRoute, RouteDecision

_ASSET_TYPES = frozenset({"native_tool", "skill", "recipe"})
_MAX_SELECTED_TURNS = 16
_MAX_DOMAINS = 8
_MAX_INTENTS = 12
_MAX_OPTIONS = 4
_MAX_ENTITIES = 16
_MAX_REQUIRED_CLAIMS = 12
_MAX_ALLOWED_ASSETS = 32
_MAX_ALLOWED_TOOLS = 64
_MAX_SHORT_TEXT = 240
_MAX_QUESTION_TEXT = 4000
_MAX_QUERY_TEXT = 1000
_CONFIDENCE_DEFAULT = 0.5

_EnumT = TypeVar("_EnumT", bound=Enum)


class ContextRelation(str, Enum):
    """현재 질문과 bounded history 후보의 의미 관계."""

    STANDALONE = "standalone"
    SAME_THREAD = "same_thread"
    RELATED_REFERENCE = "related_reference"
    TOPIC_SHIFT = "topic_shift"
    UNCLEAR = "unclear"


class EvidenceOwner(str, Enum):
    """현재 사실 evidence 수집을 소유하는 controller."""

    NONE = "none"
    PLANNER = "planner"
    ASSET = "asset"


@dataclass(frozen=True)
class ContextSelection:
    """downstream에 전달할 문맥 turn과 독립 실행 질문."""

    relation: ContextRelation
    use_prior_context: bool
    selected_turn_ids: tuple[str, ...]
    standalone_question: str
    unresolved_references: tuple[str, ...] = ()
    ignored_context_reason: str = ""


@dataclass(frozen=True)
class ClarificationPlan:
    """모호한 대상을 사용자에게 확인하기 위한 짧은 질문 계약."""

    required: bool
    question: str = ""
    options: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class FactEntity:
    """Typed entity needed by a fact source adapter."""

    kind: str
    value: str


@dataclass(frozen=True)
class FactCheckPlan:
    """현재 사실 검증의 owner·검색 입력·필수 claim 계약."""

    required: bool
    owner: EvidenceOwner
    domain: str
    entities: tuple[FactEntity, ...]
    search_query: str
    intents: tuple[str, ...] = ()
    reference_date: str = ""
    required_claims: tuple[str, ...] = ()
    freshness_required: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        """Normalize one-release programmatic string entities to typed values."""
        normalized = tuple(
            item
            if isinstance(item, FactEntity)
            else FactEntity(kind="legacy", value=str(item))
            for item in self.entities
            if isinstance(item, FactEntity) or str(item).strip()
        )
        if normalized != self.entities:
            object.__setattr__(self, "entities", normalized)

    def entity(self, kind: str) -> str:
        """Return the first exact value for a typed entity kind."""
        return next(
            (item.value for item in self.entities if item.kind == kind),
            "",
        )


@dataclass(frozen=True)
class AssetRef:
    """catalog에 있는 실행 자산의 type/name 식별자."""

    asset_type: str
    name: str


@dataclass(frozen=True)
class CapabilityPlan:
    """Mode 선택과 분리된 capability coverage/allowlist 결정."""

    coverage: CapabilityCoverage = CapabilityCoverage.NO_MATCH
    primary_asset: AssetRef | None = None
    supporting_assets: tuple[AssetRef, ...] = ()
    fallback_modes: tuple[ExecutionMode, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    """선택 mode와 그 mode 안에서 허용할 native tool 범위.

    ``primary_asset``/``allowed_assets``는 한 릴리스 동안 parser/programmatic
    caller 호환을 위한 adapter 필드다. Primary controller는
    :class:`CapabilityPlan`만 읽는다.
    """

    mode: ExecutionMode
    primary_asset: AssetRef | None = None
    allowed_assets: tuple[AssetRef, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    requires_confirmation: bool = False
    reason: str = ""
    complexity_signals: tuple[ComplexitySignal, ...] = ()


@dataclass(frozen=True)
class UnifiedTurnPlan:
    """현재 turn의 문맥·검증·실행 결정을 하나로 묶은 불변 계획."""

    original_text: str
    context: ContextSelection
    clarification: ClarificationPlan
    domains: tuple[str, ...]
    intents: tuple[str, ...]
    fact_check: FactCheckPlan
    execution: ExecutionPlan
    confidence: float
    decision_summary: str
    capability: CapabilityPlan = CapabilityPlan()
    source: str = "llm"
    catalog_fingerprint: str = ""
    # Provider 입력이 아니라 PlanGate가 선택 자산의 승인 snapshot에서 봉인한다.
    approved_asset_fingerprint: str = ""

    def __post_init__(self) -> None:
        """Legacy execution asset fields를 capability adapter로 한 번 투영한다."""
        if (
            self.capability.coverage is CapabilityCoverage.NO_MATCH
            and (self.execution.primary_asset or self.execution.allowed_assets)
        ):
            coverage = (
                CapabilityCoverage.FULL
                if self.execution.primary_asset is not None
                else CapabilityCoverage.PARTIAL
            )
            object.__setattr__(
                self,
                "capability",
                CapabilityPlan(
                    coverage=coverage,
                    primary_asset=self.execution.primary_asset,
                    supporting_assets=self.execution.allowed_assets,
                    reason=self.execution.reason,
                ),
            )

    def to_route_decision(self) -> RouteDecision:
        """기존 orchestrator가 읽는 RouteDecision을 execution.mode에서 파생한다."""
        if self.execution.mode is ExecutionMode.ANSWER_WITH_EVIDENCE:
            route = ResponseRoute.CURRENT_FACT_GUARDED_LOOP
            complexity_score = 3
        elif self.execution.mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM:
            route = ResponseRoute.COMPLEX_FACT_WORKFLOW
            complexity_score = 8
        else:
            route = ResponseRoute.STANDARD_TOOL_LOOP
            complexity_score = 0

        return RouteDecision(
            route=route,
            complexity_score=complexity_score,
            reasons=[
                self.execution.reason
                or self.decision_summary
                or f"unified_turn_plan:{self.execution.mode.value}"
            ],
            needs_current_facts=(
                self.fact_check.required or self.fact_check.freshness_required
            ),
            needs_calculation="calculation" in self.intents,
            needs_comparison_or_conditions=(
                self.execution.mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM
                and len(self.fact_check.required_claims) > 1
            ),
            needs_impact_analysis=(
                self.execution.mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM
            ),
        )

    def to_evaluator_payload(self) -> dict[str, Any]:
        """BIZ-488 evaluator가 소비하는 compact prediction shape를 반환한다."""
        primary = self.capability.primary_asset
        return {
            "context": {
                "relation": self.context.relation.value,
                "selected_turn_ids": list(self.context.selected_turn_ids),
                "standalone_question": self.context.standalone_question,
            },
            "clarification": {"required": self.clarification.required},
            "domains": list(self.domains),
            "fact_check": {
                "required": self.fact_check.required,
                "domain": self.fact_check.domain,
                "intents": list(self.fact_check.intents),
                # Keep the BIZ-488 evaluator's compact string projection while
                # exposing the typed records separately for newer evaluators.
                "entities": [item.value for item in self.fact_check.entities],
                "entity_details": [
                    {"kind": item.kind, "value": item.value}
                    for item in self.fact_check.entities
                ],
                "reference_date": self.fact_check.reference_date,
                "search_query": self.fact_check.search_query,
            },
            "execution": {
                "mode": self.execution.mode.value,
                "primary_asset": (
                    None
                    if primary is None
                    else {
                        "asset_type": primary.asset_type,
                        "name": primary.name,
                    }
                ),
            },
            "capability": {
                "coverage": self.capability.coverage.value,
                "primary_asset": (
                    None
                    if primary is None
                    else {"asset_type": primary.asset_type, "name": primary.name}
                ),
                "supporting_assets": [
                    {"asset_type": item.asset_type, "name": item.name}
                    for item in self.capability.supporting_assets
                ],
            },
        }


def _strict_object(
    properties: dict[str, dict[str, Any]],
    *,
    description: str = "",
) -> dict[str, Any]:
    """Gemini/OpenAI 양쪽이 해석할 strict object schema를 만든다."""
    fields = list(properties)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": fields,
        "additionalProperties": False,
        "propertyOrdering": fields,
    }
    if description:
        schema["description"] = description
    return schema


def _asset_schema(*, allow_none: bool) -> dict[str, Any]:
    """nullable 차이를 피하는 고정 asset object schema를 만든다."""
    asset_types = ["native_tool", "skill", "recipe"]
    if allow_none:
        asset_types.insert(0, "none")
    return _strict_object(
        {
            "asset_type": {
                "type": "string",
                "enum": asset_types,
            },
            "asset_name": {"type": "string"},
        }
    )


_CONTEXT_SCHEMA = _strict_object(
    {
        "relation": {
            "type": "string",
            "enum": [item.value for item in ContextRelation],
        },
        "use_prior_context": {"type": "boolean"},
        "selected_turn_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_SELECTED_TURNS,
        },
        "standalone_question": {"type": "string"},
        "unresolved_references": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_OPTIONS,
        },
        "ignored_context_reason": {"type": "string"},
    }
)

_CLARIFICATION_SCHEMA = _strict_object(
    {
        "required": {"type": "boolean"},
        "question": {"type": "string"},
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_OPTIONS,
        },
        "reason": {"type": "string"},
    }
)

_FACT_CHECK_SCHEMA = _strict_object(
    {
        "required": {"type": "boolean"},
        "owner": {
            "type": "string",
            "enum": [item.value for item in EvidenceOwner],
        },
        "domain": {"type": "string"},
        "intents": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_INTENTS,
        },
        "entities": {
            "type": "array",
            "items": _strict_object(
                {
                    "kind": {"type": "string"},
                    "value": {"type": "string"},
                }
            ),
            "maxItems": _MAX_ENTITIES,
        },
        "reference_date": {"type": "string"},
        "search_query": {"type": "string"},
        "required_claims": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_REQUIRED_CLAIMS,
        },
        "freshness_required": {"type": "boolean"},
        "reason": {"type": "string"},
    }
)

_CAPABILITY_SCHEMA = _strict_object(
    {
        "coverage": {
            "type": "string",
            "enum": [item.value for item in CapabilityCoverage],
        },
        "primary_asset": _asset_schema(allow_none=True),
        "supporting_assets": {
            "type": "array",
            "items": _asset_schema(allow_none=False),
            "maxItems": _MAX_ALLOWED_ASSETS,
        },
        "fallback_modes": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [item.value for item in ExecutionMode],
            },
            "maxItems": len(ExecutionMode),
        },
        "reason": {"type": "string"},
    }
)

_EXECUTION_SCHEMA = _strict_object(
    {
        "mode": {
            "type": "string",
            "enum": [item.value for item in ExecutionMode],
        },
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_ALLOWED_TOOLS,
        },
        "requires_confirmation": {"type": "boolean"},
        "complexity_signals": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [item.value for item in ComplexitySignal],
            },
            "maxItems": len(ComplexitySignal),
        },
        "reason": {"type": "string"},
    }
)

UNIFIED_TURN_PLAN_RESPONSE_SCHEMA: dict[str, Any] = _strict_object(
    {
        "context": _CONTEXT_SCHEMA,
        "clarification": _CLARIFICATION_SCHEMA,
        "domains": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_DOMAINS,
        },
        "intents": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_INTENTS,
        },
        "fact_check": _FACT_CHECK_SCHEMA,
        "capability": _CAPABILITY_SCHEMA,
        "execution": _EXECUTION_SCHEMA,
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "decision_summary": {"type": "string"},
    },
    description=(
        "One unified SimpleClaw turn plan covering context, clarification, "
        "fact verification, and execution scope."
    ),
)


def build_turn_plan_response_schema(
    *,
    allowed_tools: tuple[str, ...],
    allowed_asset_names: tuple[str, ...],
) -> dict[str, Any]:
    """현재 runtime catalog의 tool/declared asset 이름으로 schema를 좁힌다.

    정적 schema의 자유 문자열은 provider가 catalog에 없거나 contract가 선언되지
    않은 asset/tool을 구조적으로 유효한 값처럼 반환하게 만든다. 요청별 복사본만
    제한하므로 전역 schema와 parser 호환성은 유지하고, schema를 무시하는 provider
    응답은 기존 runtime boundary가 계속 fail-closed로 거부한다.
    """
    schema = deepcopy(UNIFIED_TURN_PLAN_RESPONSE_SCHEMA)
    tool_schema = schema["properties"]["execution"]["properties"][
        "allowed_tools"
    ]
    names = sorted({name.strip() for name in allowed_tools if name.strip()})
    tool_schema["maxItems"] = min(_MAX_ALLOWED_TOOLS, len(names))
    if names:
        tool_schema["items"] = {"type": "string", "enum": names}

    asset_names = sorted(
        {name.strip() for name in allowed_asset_names if name.strip()}
    )
    capability_schema = schema["properties"]["capability"]["properties"]
    capability_schema["primary_asset"]["properties"]["asset_name"]["enum"] = [
        "__none__",
        *asset_names,
    ]
    capability_schema["supporting_assets"]["items"]["properties"]["asset_name"][
        "enum"
    ] = asset_names
    return schema


def _strip_json_fence(text: str) -> str:
    """markdown JSON fence를 제거해 provider payload만 남긴다."""
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _mapping(value: object) -> Mapping[str, Any]:
    """dict 계열이 아닌 중첩 값은 빈 mapping으로 보수 처리한다."""
    return value if isinstance(value, Mapping) else {}


def _string(value: object, *, limit: int) -> str:
    """임의 값을 공백 정규화된 제한 길이 문자열로 만든다."""
    return " ".join(str(value or "").split())[:limit]


def _string_tuple(value: object, *, limit: int) -> tuple[str, ...]:
    """문자열 배열을 순서 보존·중복 제거된 tuple로 정제한다."""
    if not isinstance(value, list | tuple):
        return ()
    selected: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _string(item, limit=_MAX_QUESTION_TEXT)
        if not text or text in seen:
            continue
        selected.append(text)
        seen.add(text)
        if len(selected) >= limit:
            break
    return tuple(selected)


def _enum_value(
    enum_type: type[_EnumT],
    value: object,
    *,
    default: _EnumT,
) -> _EnumT:
    """알 수 없는 enum 문자열을 지정한 보수값으로 clamp한다."""
    try:
        return enum_type(str(value))
    except ValueError:
        return default


_LEGACY_MODE_MAP = {
    "execute_asset": ExecutionMode.DIRECT_ANSWER,
    "recipe": ExecutionMode.DIRECT_ANSWER,
    "tool_loop": ExecutionMode.ANSWER_WITH_EVIDENCE,
    "fact_check": ExecutionMode.ANSWER_WITH_EVIDENCE,
    "complex_fact": ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
}


def _execution_mode(value: object) -> ExecutionMode:
    """Legacy 7-mode payload를 4-mode wire contract로 보수 변환한다."""
    raw = str(value or "")
    if raw in _LEGACY_MODE_MAP:
        return _LEGACY_MODE_MAP[raw]
    return _enum_value(ExecutionMode, raw, default=ExecutionMode.CLARIFY)


def _complexity_signals(value: object) -> tuple[ComplexitySignal, ...]:
    if not isinstance(value, list | tuple):
        return ()
    selected: list[ComplexitySignal] = []
    for item in value:
        try:
            signal = ComplexitySignal(str(item))
        except ValueError:
            continue
        if signal not in selected:
            selected.append(signal)
    return tuple(selected)


def _confidence(value: object) -> float:
    """confidence를 숫자 [0, 1] 범위로 clamp한다."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = _CONFIDENCE_DEFAULT
    return max(0.0, min(1.0, number))


def _asset_ref(value: object) -> AssetRef | None:
    """provider sentinel 또는 evaluator asset object를 AssetRef로 변환한다."""
    if value in (None, "__none__"):
        return None
    data = _mapping(value)
    asset_type = _string(data.get("asset_type"), limit=32)
    name = _string(
        data.get("asset_name", data.get("name")),
        limit=256,
    )
    if asset_type == "none" or name == "__none__":
        return None
    if asset_type not in _ASSET_TYPES or not name:
        return None
    return AssetRef(asset_type=asset_type, name=name)


def _asset_refs(value: object) -> tuple[AssetRef, ...]:
    """allowed asset 배열에서 sentinel·중복·이형 값을 제거한다."""
    if not isinstance(value, list | tuple):
        return ()
    selected: list[AssetRef] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        asset = _asset_ref(item)
        if asset is None:
            continue
        identity = (asset.asset_type, asset.name)
        if identity in seen:
            continue
        selected.append(asset)
        seen.add(identity)
        if len(selected) >= _MAX_ALLOWED_ASSETS:
            break
    return tuple(selected)


def _fact_entities(value: object) -> tuple[FactEntity, ...]:
    """Parse typed entities while accepting one-release legacy strings."""
    if not isinstance(value, list | tuple):
        return ()
    selected: list[FactEntity] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if isinstance(item, Mapping):
            kind = _string(item.get("kind"), limit=80)
            entity_value = _string(item.get("value"), limit=_MAX_SHORT_TEXT)
        else:
            kind = "legacy"
            entity_value = _string(item, limit=_MAX_SHORT_TEXT)
        if not entity_value:
            continue
        identity = (kind or "unknown", entity_value)
        if identity in seen:
            continue
        selected.append(FactEntity(*identity))
        seen.add(identity)
        if len(selected) >= _MAX_ENTITIES:
            break
    return tuple(selected)


def parse_turn_plan_payload(
    payload: str,
    *,
    original_text: str,
    catalog_fingerprint: str = "",
) -> UnifiedTurnPlan:
    """JSON 문자열을 파싱해 semantic clamp가 적용된 UnifiedTurnPlan을 만든다."""
    data = decode_turn_plan_payload(payload)
    return parse_turn_plan_data(
        data,
        original_text=original_text,
        catalog_fingerprint=catalog_fingerprint,
    )


def decode_turn_plan_payload(payload: str) -> dict[str, Any]:
    """structured JSON을 raw boundary 검증에 사용할 object로 디코딩한다."""
    data = json.loads(_strip_json_fence(payload))
    if not isinstance(data, dict):
        raise TypeError("unified turn plan payload must be a JSON object")
    return data


def parse_turn_plan_data(
    data: Mapping[str, Any],
    *,
    original_text: str,
    catalog_fingerprint: str = "",
) -> UnifiedTurnPlan:
    """structured dict를 안전한 cross-field 불변식에 맞춰 모델로 조립한다."""
    if not isinstance(data, Mapping):
        raise TypeError("unified turn plan data must be a mapping")

    context_data = _mapping(data.get("context"))
    relation = _enum_value(
        ContextRelation,
        context_data.get("relation"),
        default=ContextRelation.UNCLEAR,
    )
    selected_ids = _string_tuple(
        context_data.get("selected_turn_ids"),
        limit=_MAX_SELECTED_TURNS,
    )
    unresolved = _string_tuple(
        context_data.get("unresolved_references"),
        limit=_MAX_OPTIONS,
    )
    use_prior_context = bool(context_data.get("use_prior_context", selected_ids))

    # standalone/topic shift/unclear는 downstream history를 절대 전달하지 않는다.
    if relation in {
        ContextRelation.STANDALONE,
        ContextRelation.TOPIC_SHIFT,
        ContextRelation.UNCLEAR,
    }:
        selected_ids = ()
        use_prior_context = False
    elif selected_ids:
        use_prior_context = True
    else:
        use_prior_context = False

    standalone_question = _string(
        context_data.get("standalone_question") or original_text,
        limit=_MAX_QUESTION_TEXT,
    )
    if not standalone_question:
        standalone_question = _string(original_text, limit=_MAX_QUESTION_TEXT)

    context = ContextSelection(
        relation=relation,
        use_prior_context=use_prior_context,
        selected_turn_ids=selected_ids,
        standalone_question=standalone_question,
        unresolved_references=unresolved,
        ignored_context_reason=_string(
            context_data.get("ignored_context_reason"),
            limit=_MAX_SHORT_TEXT,
        ),
    )

    clarification_data = _mapping(data.get("clarification"))
    clarification_required = (
        bool(clarification_data.get("required", False))
        or relation is ContextRelation.UNCLEAR
        or bool(unresolved)
    )
    clarification = ClarificationPlan(
        required=clarification_required,
        question=_string(
            clarification_data.get("question"),
            limit=_MAX_QUESTION_TEXT,
        ),
        options=_string_tuple(
            clarification_data.get("options"),
            limit=_MAX_OPTIONS,
        ),
        reason=_string(
            clarification_data.get("reason"),
            limit=_MAX_SHORT_TEXT,
        ),
    )

    execution_data = _mapping(data.get("execution"))
    mode = _execution_mode(execution_data.get("mode"))
    if clarification_required:
        mode = ExecutionMode.CLARIFY

    capability_data = _mapping(data.get("capability"))
    legacy_primary = _asset_ref(execution_data.get("primary_asset"))
    legacy_mode_payload = str(execution_data.get("mode") or "") in _LEGACY_MODE_MAP
    primary_asset = (
        _asset_ref(capability_data.get("primary_asset"))
        if "primary_asset" in capability_data and not legacy_mode_payload
        else legacy_primary
    )
    supporting_assets = _asset_refs(
        capability_data.get(
            "supporting_assets",
            execution_data.get("allowed_assets"),
        )
    )
    raw_coverage = (
        None if legacy_mode_payload else capability_data.get("coverage")
    )
    if raw_coverage is None:
        raw_coverage = "full_coverage" if legacy_primary else (
            "partial_coverage" if supporting_assets else "no_match"
        )
    coverage = _enum_value(
        CapabilityCoverage,
        raw_coverage,
        default=CapabilityCoverage.NO_MATCH,
    )
    if clarification_required:
        coverage = CapabilityCoverage.NEEDS_INPUT
        primary_asset = None
    if coverage is not CapabilityCoverage.FULL:
        primary_asset = None
    fallback_modes = tuple(
        dict.fromkeys(
            _execution_mode(item)
            for item in capability_data.get("fallback_modes", ())
        )
    ) if isinstance(capability_data.get("fallback_modes"), list | tuple) else ()
    capability = CapabilityPlan(
        coverage=coverage,
        primary_asset=primary_asset,
        supporting_assets=supporting_assets,
        fallback_modes=fallback_modes,
        reason=_string(
            capability_data.get("reason", execution_data.get("reason")),
            limit=_MAX_SHORT_TEXT,
        ),
    )

    execution = ExecutionPlan(
        mode=mode,
        primary_asset=primary_asset,
        allowed_assets=supporting_assets,
        allowed_tools=_string_tuple(
            execution_data.get("allowed_tools"),
            limit=_MAX_ALLOWED_TOOLS,
        ),
        requires_confirmation=bool(
            execution_data.get("requires_confirmation", False)
        ),
        reason=_string(
            execution_data.get("reason"),
            limit=_MAX_SHORT_TEXT,
        ),
        complexity_signals=_complexity_signals(
            execution_data.get("complexity_signals")
        ),
    )

    fact_data = _mapping(data.get("fact_check"))
    fact_required = bool(fact_data.get("required", False)) or mode in {
        ExecutionMode.ANSWER_WITH_EVIDENCE,
        ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
    }
    default_owner = (
        EvidenceOwner.PLANNER if fact_required else EvidenceOwner.NONE
    )
    owner = _enum_value(
        EvidenceOwner,
        fact_data.get("owner"),
        default=default_owner,
    )
    if fact_required and owner is EvidenceOwner.NONE:
        owner = EvidenceOwner.PLANNER
    if not fact_required:
        owner = EvidenceOwner.NONE

    fact_check = FactCheckPlan(
        required=fact_required,
        owner=owner,
        domain=(
            _string(fact_data.get("domain"), limit=80)
            or ("general" if fact_required else "none")
        ),
        intents=_string_tuple(
            fact_data.get("intents", data.get("intents")),
            limit=_MAX_INTENTS,
        ),
        entities=_fact_entities(fact_data.get("entities")),
        reference_date=_string(
            fact_data.get("reference_date"),
            limit=40,
        ),
        search_query=_string(
            fact_data.get("search_query"),
            limit=_MAX_QUERY_TEXT,
        ),
        required_claims=_string_tuple(
            fact_data.get("required_claims"),
            limit=_MAX_REQUIRED_CLAIMS,
        ),
        freshness_required=bool(
            fact_data.get("freshness_required", fact_required)
        ),
        reason=_string(
            fact_data.get("reason"),
            limit=_MAX_SHORT_TEXT,
        ),
    )

    return UnifiedTurnPlan(
        original_text=original_text or "",
        context=context,
        clarification=clarification,
        domains=_string_tuple(data.get("domains"), limit=_MAX_DOMAINS),
        intents=_string_tuple(data.get("intents"), limit=_MAX_INTENTS),
        fact_check=fact_check,
        execution=execution,
        confidence=_confidence(data.get("confidence")),
        decision_summary=_string(
            data.get("decision_summary"),
            limit=_MAX_SHORT_TEXT,
        ),
        capability=capability,
        source="llm",
        catalog_fingerprint=catalog_fingerprint,
    )
