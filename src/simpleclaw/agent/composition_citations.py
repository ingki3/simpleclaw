"""Composer citation metadata의 deterministic visibility canonicalization."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from .composition_contracts import CompositionInputV1, DraftResponseV1

CITATION_CANONICALIZATION_POLICY_VERSION = "provider_order_no_prune_v3"


def projected_scalar_literal_pattern(
    value: JsonValue,
) -> re.Pattern[str] | None:
    """Guard와 canonicalizer가 공유하는 exact-visible scalar pattern이다."""
    if isinstance(value, dict | list):
        return None
    if value is None or isinstance(value, bool):
        rendered = json.dumps(value)
    else:
        rendered = str(value).strip()
    if not rendered:
        return None
    escaped = re.escape(rendered)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return re.compile(rf"(?<![\d.+%-]){escaped}(?![\d.+%-])")
    if value is None or isinstance(value, bool):
        return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]+", rendered):
        return re.compile(
            rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
    return re.compile(escaped, re.IGNORECASE)


def projected_scalar_is_visible(content: str, value: JsonValue) -> bool:
    """Projected scalar의 canonical literal이 content에 exact-visible한지 판정한다."""
    pattern = projected_scalar_literal_pattern(value)
    return pattern is not None and pattern.search(content) is not None


def canonical_visible_cited_paths(
    content: str,
    provider_paths: Sequence[str],
    concrete: Mapping[str, JsonValue],
) -> tuple[str, ...] | None:
    """Safe provider set의 visible subset을 projection 순서로 반환한다.

    ``None``은 canonicalization하면 안 되는 malformed citation metadata를 뜻한다.
    호출자는 원본을 Guard에 전달해 기존 stable rejection을 보존해야 한다.
    """
    if len(provider_paths) != len(set(provider_paths)):
        return None
    selected = set(provider_paths)
    if any(
        "[*]" in path
        or path not in concrete
        or projected_scalar_literal_pattern(concrete[path]) is None
        for path in provider_paths
    ):
        return None
    return tuple(
        path
        for path, value in concrete.items()
        if path in selected and projected_scalar_is_visible(content, value)
    )


def canonicalize_draft_citations(
    value: CompositionInputV1,
    draft: DraftResponseV1,
) -> DraftResponseV1:
    """Guard 전 provider citation set/order를 변경하지 않는다."""
    return draft
