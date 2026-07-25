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
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

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


class ExecutionMode(str, Enum):
    """한 turn에서 선택할 유일한 상위 실행 경로."""

    CLARIFY = "clarify"
    DIRECT_ANSWER = "direct_answer"
    EXECUTE_ASSET = "execute_asset"
    TOOL_LOOP = "tool_loop"
    FACT_CHECK = "fact_check"
    COMPLEX_FACT = "complex_fact"
    RECIPE = "recipe"


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
class FactCheckPlan:
    """현재 사실 검증의 owner·검색 입력·필수 claim 계약."""

    required: bool
    owner: EvidenceOwner
    domain: str
    entities: tuple[str, ...]
    search_query: str
    required_claims: tuple[str, ...] = ()
    freshness_required: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AssetRef:
    """catalog에 있는 실행 자산의 type/name 식별자."""

    asset_type: str
    name: str


@dataclass(frozen=True)
class ExecutionPlan:
    """선택 mode와 그 mode 안에서 허용할 자산·native tool 범위."""

    mode: ExecutionMode
    primary_asset: AssetRef | None
    allowed_assets: tuple[AssetRef, ...]
    allowed_tools: tuple[str, ...]
    requires_confirmation: bool
    reason: str


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
    source: str = "llm"
    catalog_fingerprint: str = ""

    def to_route_decision(self) -> RouteDecision:
        """기존 orchestrator가 읽는 RouteDecision을 execution.mode에서 파생한다."""
        if self.execution.mode is ExecutionMode.FACT_CHECK:
            route = ResponseRoute.CURRENT_FACT_GUARDED_LOOP
            complexity_score = 3
        elif self.execution.mode is ExecutionMode.COMPLEX_FACT:
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
                self.execution.mode is ExecutionMode.COMPLEX_FACT
                and len(self.fact_check.required_claims) > 1
            ),
            needs_impact_analysis=(
                self.execution.mode is ExecutionMode.COMPLEX_FACT
            ),
        )

    def to_evaluator_payload(self) -> dict[str, Any]:
        """BIZ-488 evaluator가 소비하는 compact prediction shape를 반환한다."""
        primary = self.execution.primary_asset
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
                "entities": list(self.fact_check.entities),
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
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_ENTITIES,
        },
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

_EXECUTION_SCHEMA = _strict_object(
    {
        "mode": {
            "type": "string",
            "enum": [item.value for item in ExecutionMode],
        },
        "primary_asset": _asset_schema(allow_none=True),
        "allowed_assets": {
            "type": "array",
            "items": _asset_schema(allow_none=False),
            "maxItems": _MAX_ALLOWED_ASSETS,
        },
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_ALLOWED_TOOLS,
        },
        "requires_confirmation": {"type": "boolean"},
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


def parse_turn_plan_payload(
    payload: str,
    *,
    original_text: str,
    catalog_fingerprint: str = "",
) -> UnifiedTurnPlan:
    """JSON 문자열을 파싱해 semantic clamp가 적용된 UnifiedTurnPlan을 만든다."""
    data = json.loads(_strip_json_fence(payload))
    if not isinstance(data, dict):
        raise ValueError("unified turn plan payload must be a JSON object")
    return parse_turn_plan_data(
        data,
        original_text=original_text,
        catalog_fingerprint=catalog_fingerprint,
    )


def parse_turn_plan_data(
    data: Mapping[str, Any],
    *,
    original_text: str,
    catalog_fingerprint: str = "",
) -> UnifiedTurnPlan:
    """structured dict를 안전한 cross-field 불변식에 맞춰 모델로 조립한다."""
    if not isinstance(data, Mapping):
        raise ValueError("unified turn plan data must be a mapping")

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
    mode = _enum_value(
        ExecutionMode,
        execution_data.get("mode"),
        default=ExecutionMode.CLARIFY,
    )
    if clarification_required:
        mode = ExecutionMode.CLARIFY

    primary_asset = _asset_ref(execution_data.get("primary_asset"))
    allowed_assets = list(_asset_refs(execution_data.get("allowed_assets")))
    if primary_asset is not None and primary_asset not in allowed_assets:
        allowed_assets.insert(0, primary_asset)
    if mode in {ExecutionMode.CLARIFY, ExecutionMode.DIRECT_ANSWER}:
        primary_asset = None

    execution = ExecutionPlan(
        mode=mode,
        primary_asset=primary_asset,
        allowed_assets=tuple(allowed_assets[:_MAX_ALLOWED_ASSETS]),
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
    )

    fact_data = _mapping(data.get("fact_check"))
    fact_required = bool(fact_data.get("required", False)) or mode in {
        ExecutionMode.FACT_CHECK,
        ExecutionMode.COMPLEX_FACT,
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
    if not fact_required and mode is not ExecutionMode.RECIPE:
        owner = EvidenceOwner.NONE

    fact_check = FactCheckPlan(
        required=fact_required,
        owner=owner,
        domain=(
            _string(fact_data.get("domain"), limit=80)
            or ("general" if fact_required else "none")
        ),
        entities=_string_tuple(
            fact_data.get("entities"),
            limit=_MAX_ENTITIES,
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
        source="llm",
        catalog_fingerprint=catalog_fingerprint,
    )
