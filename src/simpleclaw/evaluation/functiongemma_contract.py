"""FunctionGemma intent/asset PoC의 축소 native function 계약."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simpleclaw.agent.turn_plan import ContextRelation, ExecutionMode

FUNCTION_NAME = "classify_intent_and_select_asset"
NO_ASSET = "__none__"
MAX_CANDIDATES = 12
MAX_DOMAINS = 8
MAX_INTENTS = 12


class FunctionCallContractError(ValueError):
    """모델 출력이 strict schema 또는 현재 candidate 경계를 위반했다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CandidateAsset:
    """한 case에 노출되는 compact asset."""

    asset_id: str
    asset_type: str
    name: str
    description: str = ""
    domains: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompactIntentCall:
    """검증을 마친 축소 intent/asset function arguments."""

    context_relation: str
    execution_mode: str
    domains: tuple[str, ...]
    intents: tuple[str, ...]
    primary_asset: str
    fallback_required: bool

    def to_arguments(self) -> dict[str, Any]:
        return {
            "context_relation": self.context_relation,
            "execution_mode": self.execution_mode,
            "domains": list(self.domains),
            "intents": list(self.intents),
            "primary_asset": self.primary_asset,
            "fallback_required": self.fallback_required,
        }

    def to_native_call(self) -> dict[str, Any]:
        return {"name": FUNCTION_NAME, "arguments": self.to_arguments()}


def _strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    fields = list(properties)
    return {
        "type": "object",
        "properties": properties,
        "required": fields,
        "additionalProperties": False,
        "propertyOrdering": fields,
    }


FUNCTION_DECLARATION: dict[str, Any] = {
    "name": FUNCTION_NAME,
    "description": (
        "Classify the current turn and select exactly one asset from the supplied "
        "candidate IDs, or __none__."
    ),
    "parameters": _strict_object(
        {
            "context_relation": {
                "type": "string",
                "enum": [item.value for item in ContextRelation],
            },
            "execution_mode": {
                "type": "string",
                "enum": [item.value for item in ExecutionMode],
            },
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_DOMAINS,
            },
            "intents": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_INTENTS,
            },
            "primary_asset": {"type": "string"},
            "fallback_required": {"type": "boolean"},
        }
    ),
}


def candidate_id(asset_type: str, name: str) -> str:
    """Planner asset을 경로가 없는 stable ID로 표현한다."""
    return f"{asset_type}:{name}"


def _string_list(value: object, *, field: str, limit: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise FunctionCallContractError(f"schema.invalid_{field}")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise FunctionCallContractError(f"schema.invalid_{field}")
        clean = item.strip().lower()
        if clean not in normalized:
            normalized.append(clean)
    if len(normalized) > limit:
        raise FunctionCallContractError(f"schema.{field}_limit")
    return tuple(normalized)


def _decode_payload(payload: object) -> Mapping[str, object]:
    if isinstance(payload, str):
        if "<start_function_call>" in payload:
            return _decode_functiongemma_call(payload)
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FunctionCallContractError("schema.invalid_json") from exc
    if not isinstance(payload, Mapping):
        raise FunctionCallContractError("schema.not_object")
    return payload


def _decode_functiongemma_call(payload: str) -> Mapping[str, object]:
    match = re.search(
        rf"<start_function_call>\s*call:{FUNCTION_NAME}\{{(.*?)\}}"
        r"\s*<end_function_call>",
        payload,
        flags=re.DOTALL,
    )
    if match is None:
        raise FunctionCallContractError("schema.invalid_native_function_call")
    body = match.group(1)

    def escaped(field: str) -> str:
        value = re.search(
            rf"(?:^|,){field}:<escape>(.*?)<escape>(?:,|$)",
            body,
            flags=re.DOTALL,
        )
        if value is None:
            raise FunctionCallContractError(f"schema.invalid_{field}")
        return value.group(1)

    def escaped_array(field: str) -> list[str]:
        value = re.search(
            rf"(?:^|,){field}:\[(.*?)\](?:,|$)",
            body,
            flags=re.DOTALL,
        )
        if value is None:
            raise FunctionCallContractError(f"schema.invalid_{field}")
        content = value.group(1)
        if not content.strip():
            return []
        items = re.findall(r"<escape>(.*?)<escape>", content, flags=re.DOTALL)
        if not items:
            raise FunctionCallContractError(f"schema.invalid_{field}")
        return items

    boolean = re.search(
        r"(?:^|,)fallback_required:(true|false)(?:,|$)",
        body,
    )
    if boolean is None:
        raise FunctionCallContractError("schema.invalid_fallback_required")
    return {
        "context_relation": escaped("context_relation"),
        "execution_mode": escaped("execution_mode"),
        "domains": escaped_array("domains"),
        "intents": escaped_array("intents"),
        "primary_asset": escaped("primary_asset"),
        "fallback_required": boolean.group(1) == "true",
    }


def parse_function_call(
    payload: object,
    *,
    candidate_ids: Sequence[str],
) -> CompactIntentCall:
    """native call을 strict parse하고 현재 case candidate 경계를 검증한다."""
    if len(candidate_ids) > MAX_CANDIDATES:
        raise FunctionCallContractError("boundary.too_many_candidates")
    if len(set(candidate_ids)) != len(candidate_ids):
        raise FunctionCallContractError("boundary.duplicate_candidate")

    outer = _decode_payload(payload)
    if set(outer) == {"name", "arguments"}:
        if outer["name"] != FUNCTION_NAME:
            raise FunctionCallContractError("schema.unknown_function")
        arguments = _decode_payload(outer["arguments"])
    else:
        arguments = outer

    expected = {
        "context_relation",
        "execution_mode",
        "domains",
        "intents",
        "primary_asset",
        "fallback_required",
    }
    if set(arguments) != expected:
        raise FunctionCallContractError("schema.fields_mismatch")

    relation = arguments["context_relation"]
    mode = arguments["execution_mode"]
    primary = arguments["primary_asset"]
    fallback = arguments["fallback_required"]
    if relation not in {item.value for item in ContextRelation}:
        raise FunctionCallContractError("schema.invalid_context_relation")
    if mode not in {item.value for item in ExecutionMode}:
        raise FunctionCallContractError("schema.invalid_execution_mode")
    if not isinstance(primary, str) or not primary:
        raise FunctionCallContractError("schema.invalid_primary_asset")
    if not isinstance(fallback, bool):
        raise FunctionCallContractError("schema.invalid_fallback_required")
    allowed = {NO_ASSET, *candidate_ids}
    if primary not in allowed:
        raise FunctionCallContractError("boundary.unknown_asset")
    if primary == NO_ASSET and mode in {"execute_asset", "recipe"} and not fallback:
        raise FunctionCallContractError("boundary.missing_fallback")

    return CompactIntentCall(
        context_relation=str(relation),
        execution_mode=str(mode),
        domains=_string_list(arguments["domains"], field="domains", limit=MAX_DOMAINS),
        intents=_string_list(arguments["intents"], field="intents", limit=MAX_INTENTS),
        primary_asset=primary,
        fallback_required=fallback,
    )


def canonical_call_json(call: CompactIntentCall) -> str:
    """학습 completion과 fingerprint가 공유하는 canonical native call JSON."""
    return json.dumps(
        call.to_native_call(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def functiongemma_tool_schema() -> dict[str, Any]:
    """Hugging Face/MLX chat template이 소비하는 tool wrapper."""
    return {"type": "function", "function": FUNCTION_DECLARATION}
