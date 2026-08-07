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
    "provider error",
    "provider_error",
    "upstream error",
    "upstream timeout",
    "schema error",
    "exception",
    "trace id",
    "traceback",
    "secret",
    "token=",
    "주민번호",
    "개인정보",
)
_LIMITATION_MARKERS = (
    "불확실",
    "확인되지",
    "확인할 수 없",
    "알 수 없",
    "제한",
    "추정",
    "unknown",
    "uncertain",
    "unverified",
    "could not verify",
    "cannot verify",
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(r"\b[A-Z0-9_-]{6,}\b", re.IGNORECASE)


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


def _item_index(path: str) -> int | None:
    match = re.search(r"(?:^|\.)items\[(\d+)\]", path)
    return int(match.group(1)) if match else None


def _required_item_indices(
    concrete: dict[str, JsonValue],
    top_n: int,
) -> set[int]:
    """실제 projection에 존재하는 top-N item index만 완전성 대상으로 삼는다."""
    available = {
        index
        for path in concrete
        if (index := _item_index(path)) is not None and index < top_n
    }
    return set(sorted(available)[:top_n])


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
    identifier_source = _URL_RE.sub("", content)
    if _EMAIL_RE.search(content) or _IDENTIFIER_RE.search(identifier_source):
        # 긴 identifier는 projected scalar로 명시 인용된 경우에만 허용한다.
        identifiers = set(_IDENTIFIER_RE.findall(identifier_source))
        projected_identifiers = {
            str(item)
            for item in flatten_public_facts(value.public_facts).values()
            if isinstance(item, str)
        }
        if _EMAIL_RE.search(content) or not identifiers <= projected_identifiers:
            return _rejected("private_or_unprojected_identifier")

    concrete = flatten_public_facts(value.public_facts)
    if not draft.cited_paths:
        return _rejected("citations_required")
    top_n = _requested_top_n(value.question)
    cited_values: dict[str, JsonValue] = {}
    cited_item_indices: set[int] = set()
    for path in draft.cited_paths:
        if "[*]" in path or path not in concrete:
            return _rejected("citation_not_projected")
        if top_n is not None:
            index = _item_index(path)
            if index is not None and index >= top_n:
                return _rejected("citation_outside_requested_scope")
            if index is not None:
                cited_item_indices.add(index)
        cited = concrete[path]
        cited_values[path] = cited
        if isinstance(cited, str) and len(cited.strip()) >= 2:
            if cited.strip().casefold() not in lowered:
                return _rejected("cited_value_not_rendered")
    if top_n is not None and cited_item_indices != _required_item_indices(
        concrete, top_n
    ):
        return _rejected("requested_scope_not_fully_cited")

    # Content에 보이는 projected scalar는 해당 concrete path가 반드시 인용돼야 한다.
    # 동일 값이 여러 path에 있을 수 있으므로 한 path라도 cited면 grounded로 본다.
    for path, projected in concrete.items():
        if not isinstance(projected, str):
            continue
        rendered = str(projected).strip()
        if len(rendered) < 2:
            continue
        if (
            rendered.casefold() in lowered
            and rendered.casefold() not in value.question.casefold()
            and not any(
                cited_value == projected
                for cited_value in cited_values.values()
            )
        ):
            return _rejected("rendered_value_not_cited")

    limitation_values = {
        f"unresolved_claims[{index}]": claim
        for index, claim in enumerate(value.unresolved_claims)
    }
    if value.unresolved_claims and not draft.limitation_paths:
        return _rejected("unresolved_claim_not_limited")
    if any(path not in limitation_values for path in draft.limitation_paths):
        return _rejected("limitation_not_projected")
    if value.unresolved_claims and set(draft.limitation_paths) != set(
        limitation_values
    ):
        return _rejected("unresolved_claim_not_limited")
    if value.unresolved_claims and not any(
        marker in lowered for marker in _LIMITATION_MARKERS
    ):
        return _rejected("limitation_not_rendered")

    allowed_urls: set[str] = set()
    allowed_numbers: set[str] = _list_lengths(value.public_facts)
    for projected in cited_values.values():
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
