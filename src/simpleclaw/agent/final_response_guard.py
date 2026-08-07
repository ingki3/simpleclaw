"""중앙 composer draft의 grounding·노출·요청 범위를 검증한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus

from .composition_contracts import CompositionInputV1, DraftResponseV1
from .composition_projection import flatten_public_facts

_URL_RE = re.compile(r"https?://[^\s)>\]}]+")
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?")
_TOP_N_PATTERNS = (
    re.compile(r"(?:상위|앞)\s*(\d+)", re.IGNORECASE),
    re.compile(r"\btop\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:개\s*)?팀(?:만)?", re.IGNORECASE),
)
_RAW_MARKERS = (
    "```json",
    "asset_result.v1",
    "composition_input.v1",
    "provider_payload",
    "raw_payload",
    "diagnostic",
    "traceback",
    "secret",
    "token=",
)


@dataclass(frozen=True, slots=True)
class GuardResult:
    """Response guard의 명시적 승인 여부와 stable 거부 code다."""

    accepted: bool
    code: str


def _rejected(code: str) -> GuardResult:
    return GuardResult(accepted=False, code=code)


def _tokens(value: Any) -> tuple[set[str], set[str]]:
    """Projected value에서 URL과 numeric grounding token을 수집한다."""
    text = str(value)
    urls = set(_URL_RE.findall(text))
    without_urls = _URL_RE.sub("", text)
    return urls, set(_NUMBER_RE.findall(without_urls))


def _list_lengths(value: JsonValue) -> set[str]:
    lengths: set[str] = set()
    if isinstance(value, list):
        lengths.add(str(len(value)))
        for item in value:
            lengths.update(_list_lengths(item))
    elif isinstance(value, dict):
        for item in value.values():
            lengths.update(_list_lengths(item))
    return lengths


def _requested_top_n(question: str) -> int | None:
    for pattern in _TOP_N_PATTERNS:
        match = pattern.search(question)
        if match:
            return int(match.group(1))
    return None


def guard_final_response(
    value: CompositionInputV1,
    draft: DraftResponseV1,
) -> GuardResult:
    """Projection 밖 사실·raw 진단·unsafe effect의 final 승격을 거부한다."""
    if value.result_status is not AssetResultStatus.RESOLVED or (
        value.effect_status not in {EffectStatus.NONE, EffectStatus.VERIFIED}
    ):
        return _rejected("unsafe_result")
    content = draft.content.strip()
    if not content or len(content) > 3_500:
        return _rejected("invalid_length")
    lowered = content.casefold()
    if content.startswith(("{", "[")) or any(
        marker in lowered for marker in _RAW_MARKERS
    ):
        return _rejected("raw_contract_exposed")

    concrete = flatten_public_facts(value.public_facts)
    if not draft.cited_paths:
        return _rejected("citations_required")
    top_n = _requested_top_n(value.question)
    for path in draft.cited_paths:
        if "[*]" in path or path not in concrete:
            return _rejected("citation_not_projected")
        if top_n is not None:
            match = re.search(r"(?:^|\.)items\[(\d+)\]", path)
            if match and int(match.group(1)) >= top_n:
                return _rejected("citation_outside_requested_scope")
        cited = concrete[path]
        if isinstance(cited, str) and len(cited.strip()) >= 2:
            if cited.strip().casefold() not in lowered:
                return _rejected("cited_value_not_rendered")

    limitation_values = {
        f"unresolved_claims[{index}]": claim
        for index, claim in enumerate(value.unresolved_claims)
    }
    if value.unresolved_claims and not draft.limitation_paths:
        return _rejected("unresolved_claim_not_limited")
    if any(path not in limitation_values for path in draft.limitation_paths):
        return _rejected("limitation_not_projected")

    allowed_urls: set[str] = set()
    allowed_numbers: set[str] = _list_lengths(value.public_facts)
    for projected in concrete.values():
        urls, numbers = _tokens(projected)
        allowed_urls.update(urls)
        allowed_numbers.update(numbers)
    content_urls = set(_URL_RE.findall(content))
    if not content_urls <= allowed_urls:
        return _rejected("ungrounded_url")
    content_numbers = set(_NUMBER_RE.findall(_URL_RE.sub("", content)))
    if not content_numbers <= allowed_numbers:
        return _rejected("ungrounded_number")
    return GuardResult(accepted=True, code="accepted")
