"""Typed asset result를 안전한 사용자 텍스트로 투영하는 generic 경계."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SAFE_EMPTY_RESULT = "요청을 처리했지만 안전하게 표시할 수 있는 텍스트 결과가 없습니다."
MAX_TYPED_PRESENTATION_CHARS = 3_500
_PREFERRED_TEXT_KEYS = ("answer", "result", "content", "text", "message", "summary")
_SAFE_TYPED_STATUSES = frozenset({"completed", "resolved"})
_SAFE_EFFECT_STATUSES = frozenset({"none", "verified"})


def _preferred_text(payload: Mapping[str, Any]) -> str | None:
    for key in _PREFERRED_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _bounded_typed_text(value: str) -> str:
    if len(value) <= MAX_TYPED_PRESENTATION_CHARS:
        return value
    return value[: MAX_TYPED_PRESENTATION_CHARS - 1].rstrip() + "…"


def compose_user_facing_asset_result(
    *,
    payload: Mapping[str, Any],
    result_status: str,
    effect_status: str,
) -> str:
    """Typed safety gate 뒤 asset-owned preferred text만 bounded하게 반환한다.

    ``data``의 업무별 구조는 해석하지 않는다. 자산이 만든 presentation text가
    있으면 그 값만 사용하고, typed 안전 조건이 하나라도 불명확하면 fail-closed한다.
    legacy payload는 기존 scalar fallback 동작을 유지한다.
    """
    if payload.get("schema") == "asset_result.v1":
        if (
            result_status != "resolved"
            or effect_status not in _SAFE_EFFECT_STATUSES
            or payload.get("status") not in _SAFE_TYPED_STATUSES
            or payload.get("side_effect") is not False
        ):
            return SAFE_EMPTY_RESULT
        preferred = _preferred_text(payload)
        data = payload.get("data")
        if preferred is None and isinstance(data, Mapping):
            preferred = _preferred_text(data)
        return (
            _bounded_typed_text(preferred)
            if preferred is not None
            else SAFE_EMPTY_RESULT
        )

    preferred = _preferred_text(payload)
    if preferred is not None:
        return preferred
    strings = [
        value.strip()
        for value in payload.values()
        if isinstance(value, str) and value.strip()
    ]
    if len(strings) == 1:
        return strings[0]
    lines = [
        f"- {key}: {value}"
        for key, value in sorted(payload.items())
        if isinstance(value, (str, int, float, bool))
    ]
    if lines:
        return "처리 결과입니다.\n" + "\n".join(lines)
    return SAFE_EMPTY_RESULT
