"""Selector 응답을 안전한 top-k 후보 목록으로 정규화한다.

Function selector는 실행 여부를 결정하는 권한을 갖지 않는다. 이 모듈은 LLM의
function-call 결과를 후보 축소용 자료구조로 바꾸고, recipe 과선택·낮은 confidence·
function-call 누락·모호한 요청을 main LLM 판단으로 되돌리는 guardrail을 제공한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from simpleclaw.llm.models import ToolCall

AssetType = Literal["skill", "recipe"]

_SELECTOR_TOOL_NAME = "select_assets"
_DEFAULT_MIN_CONFIDENCE = 0.5
_AMBIGUOUS_UTTERANCES = {
    "이거 좀 정리해줘",
    "정리해줘",
    "요약해줘",
    "알아서 해줘",
    "처리해줘",
}
_RECIPE_ACTIVATION_KEYWORDS = (
    "실행",
    "실행해",
    "돌려줘",
    "브리핑",
    "리포트",
    "보고서",
    "보내줘",
    "매일",
    "매주",
    "정기",
    "장 마감",
)


@dataclass(frozen=True)
class SelectorAsset:
    """selector 후보군에 노출되는 스킬/레시피 자산 메타데이터."""

    type: AssetType
    name: str
    description: str = ""
    source: str = ""
    trigger: str = ""
    commands_count: int = 0
    parameters_count: int = 0
    steps_count: int = 0


@dataclass(frozen=True)
class AssetCandidate:
    """selector가 고른 단일 후보.

    confidence는 LLM self-report라 최종 신뢰도는 아니지만, 낮은 값은 후보 축소기에서도
    fallback이 필요한 신호로 취급한다.
    """

    type: AssetType
    name: str
    confidence: float
    reason: str = ""


@dataclass(frozen=True)
class AssetSelectionResult:
    """guardrail 적용 후 main LLM에 전달할 후보 목록과 fallback 신호."""

    selected: list[AssetCandidate] = field(default_factory=list)
    fallback_required: bool = False
    fallback_reason: str = ""
    used_tool_call: bool = False


def normalize_selector_response(
    *,
    user_message: str,
    known_assets: list[SelectorAsset],
    response_text: str = "",
    tool_calls: list[ToolCall] | None = None,
    top_k: int = 5,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> AssetSelectionResult:
    """LLM selector 응답을 보수적인 top-k 후보 결과로 변환한다.

    Native function-call이 없으면 텍스트 JSON을 파싱할 수 있어도 selector 신뢰도를 낮게
    보고 fallback을 요구한다. recipe는 실행 의도가 명시된 경우에만 후보로 남긴다.
    """

    raw_selection, used_tool_call = _extract_selection_payload(response_text, tool_calls)
    known_keys = {(asset.type, asset.name) for asset in known_assets}
    candidates = _coerce_candidates(raw_selection.get("selected"), known_keys, min_confidence)
    reasons: list[str] = []

    if not used_tool_call:
        reasons.append("missing_function_call")

    if _is_ambiguous_intent(user_message):
        if any(candidate.type == "recipe" for candidate in candidates):
            reasons.append("recipe_guardrail")
        candidates = [candidate for candidate in candidates if candidate.type != "recipe"]
        reasons.append("ambiguous_intent")
    elif not _has_explicit_recipe_intent(user_message):
        filtered = [candidate for candidate in candidates if candidate.type != "recipe"]
        if len(filtered) != len(candidates):
            reasons.append("recipe_guardrail")
        candidates = filtered

    candidates = candidates[: max(top_k, 0)]

    if raw_selection.get("fallback"):
        fallback_reason = str(raw_selection.get("fallback_reason") or "selector_requested_fallback")
        reasons.append(fallback_reason)
    if not candidates:
        reasons.append("empty_selection")
    if any(candidate.confidence < min_confidence for candidate in candidates):
        reasons.append("low_confidence")

    fallback_required = _needs_fallback(reasons)
    return AssetSelectionResult(
        selected=candidates,
        fallback_required=fallback_required,
        fallback_reason=", ".join(dict.fromkeys(reason for reason in reasons if reason)),
        used_tool_call=used_tool_call,
    )


def _extract_selection_payload(
    response_text: str,
    tool_calls: list[ToolCall] | None,
) -> tuple[dict[str, Any], bool]:
    """function-call 우선으로 selector payload를 추출한다."""

    if tool_calls:
        for call in tool_calls:
            if call.name == _SELECTOR_TOOL_NAME and isinstance(call.arguments, dict):
                return dict(call.arguments), True
        return {"selected": [], "fallback": True, "fallback_reason": "unexpected_tool_call"}, True

    try:
        parsed = json.loads(response_text) if response_text else {}
    except json.JSONDecodeError:
        return {"selected": [], "fallback": True, "fallback_reason": "parse_failed"}, False
    if isinstance(parsed, dict):
        return parsed, False
    return {"selected": [], "fallback": True, "fallback_reason": "parse_failed"}, False


def _coerce_candidates(
    raw_items: Any,
    known_keys: set[tuple[str, str]],
    min_confidence: float,
) -> list[AssetCandidate]:
    """알 수 없는 asset과 잘못된 shape을 제거하고 confidence 순으로 정렬한다."""

    if not isinstance(raw_items, list):
        return []

    candidates: list[AssetCandidate] = []
    seen: set[tuple[AssetType, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        asset_type = item.get("type")
        name = item.get("name")
        if asset_type not in ("skill", "recipe") or not isinstance(name, str):
            continue
        key = (asset_type, name)
        if key not in known_keys or key in seen:
            continue
        try:
            confidence = float(item.get("confidence", min_confidence))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        candidates.append(
            AssetCandidate(
                type=asset_type,
                name=name,
                confidence=confidence,
                reason=str(item.get("reason") or ""),
            )
        )
        seen.add(key)
    return sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True)


def _is_ambiguous_intent(user_message: str) -> bool:
    """대명사 중심·짧은 위임 요청을 selector 단독 판단 금지 신호로 본다."""

    normalized = " ".join(user_message.strip().split())
    if normalized in _AMBIGUOUS_UTTERANCES:
        return True
    return len(normalized) <= 12 and any(token in normalized for token in ("이거", "그거", "저거"))


def _has_explicit_recipe_intent(user_message: str) -> bool:
    """recipe 실행 의도를 보수적으로 감지한다."""

    normalized = user_message.lower()
    return any(keyword in normalized for keyword in _RECIPE_ACTIVATION_KEYWORDS)


def _needs_fallback(reasons: list[str]) -> bool:
    """fallback reason 중 최종 판단을 main LLM으로 돌려야 하는 항목을 판정한다."""

    hard_reasons = {
        "missing_function_call",
        "ambiguous_intent",
        "empty_selection",
        "low_confidence",
        "parse_failed",
        "unexpected_tool_call",
        "selector_requested_fallback",
    }
    return any(reason in hard_reasons or reason.startswith("parse_") for reason in reasons)
