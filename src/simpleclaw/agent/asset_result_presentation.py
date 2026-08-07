"""Typed asset result를 안전한 사용자 텍스트로 투영하는 generic 경계."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SAFE_EMPTY_RESULT = "요청을 처리했지만 안전하게 표시할 수 있는 텍스트 결과가 없습니다."
_PREFERRED_TEXT_KEYS = ("answer", "result", "content", "text", "message", "summary")


def _preferred_text(payload: Mapping[str, Any]) -> str | None:
    for key in _PREFERRED_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def compose_user_facing_asset_result(
    *,
    payload: Mapping[str, Any],
    result_status: str,
    effect_status: str,
) -> str:
    """Legacy scalar presentation만 유지하고 typed final ownership은 거부한다.

    V4 ``asset_result.v1``은 중앙 composer가 contract-declared projection으로만
    발화해야 한다. 이 compatibility 함수가 typed ``answer/content/text``를 다시
    최종 문장으로 승격하지 못하게 항상 fail-closed한다.
    """
    del result_status, effect_status
    if payload.get("schema") == "asset_result.v1":
        return SAFE_EMPTY_RESULT

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
