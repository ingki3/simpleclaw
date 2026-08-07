"""Typed asset result를 안전한 사용자 텍스트로 투영하는 generic 경계."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SAFE_EMPTY_RESULT = "요청을 처리했지만 안전하게 표시할 수 있는 텍스트 결과가 없습니다."
MAX_TYPED_PRESENTATION_CHARS = 3_500
_PREFERRED_TEXT_KEYS = ("answer", "result", "content", "text", "message", "summary")
_SAFE_TYPED_STATUSES = frozenset({"completed", "resolved"})
_SAFE_EFFECT_STATUSES = frozenset({"none", "verified"})
_TYPED_METADATA_KEYS = frozenset(
    {"schema", "status", "side_effect", "resolved_claims", "unresolved_claims"}
)
_COMPAT_PRIVATE_KEY_MARKERS = (
    "credential",
    "diagnostic",
    "email",
    "error",
    "internal",
    "password",
    "private",
    "prompt",
    "provider",
    "raw",
    "secret",
    "token",
)


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


def _flatten_typed_facts(
    value: Any,
    *,
    path: str = "",
    lines: list[str] | None = None,
) -> list[str]:
    """Compat 전용으로 typed data를 bounded path/value 목록으로 렌더링한다."""
    output = [] if lines is None else lines
    if len(output) >= 100:
        return output
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or key in _TYPED_METADATA_KEYS
                or any(
                    marker in key.casefold()
                    for marker in _COMPAT_PRIVATE_KEY_MARKERS
                )
            ):
                continue
            child_path = f"{path}.{key}" if path else key
            _flatten_typed_facts(item, path=child_path, lines=output)
    elif isinstance(value, list):
        for index, item in enumerate(value[:20]):
            _flatten_typed_facts(item, path=f"{path}[{index}]", lines=output)
    elif value is None or isinstance(value, str | int | float | bool):
        rendered = str(value).strip()
        if path and rendered:
            output.append(f"- {path}: {rendered}")
    return output


def compose_user_facing_asset_result(
    *,
    payload: Mapping[str, Any],
    result_status: str,
    effect_status: str,
) -> str:
    """Deprecated compatibility mode의 안전한 기존 asset presentation을 유지한다.

    중앙 mode는 이 함수를 import하거나 호출하지 않는다. Rollout 승인 전 compat
    mode만 기존 preferred text를 유지하며, 새 typed asset에 prose가 없으면 bounded
    data path/value 목록을 반환해 generic 오류로 퇴행하지 않게 한다.
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
        if preferred is not None:
            return _bounded_typed_text(preferred)
        lines = _flatten_typed_facts(data if data is not None else payload)
        if lines:
            return _bounded_typed_text("처리 결과입니다.\n" + "\n".join(lines))
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
