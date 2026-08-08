"""중앙 composer draft의 grounding·노출·요청 범위를 검증한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus

from .composition_citations import (
    projected_scalar_is_visible,
    projected_scalar_literal_pattern,
)
from .composition_contracts import CompositionInputV1, DraftResponseV1
from .composition_projection import flatten_public_facts

_URL_RE = re.compile(r"https?://[^\s)>\]}]+")
_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?")
_TOP_N_PATTERNS = (
    re.compile(r"(?:상위|앞)\s*(\d+)", re.IGNORECASE),
    re.compile(r"\btop\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d+)\s*[^\W\d_]+만\b", re.IGNORECASE),
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
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_SAFE_PUNCTUATION_RE = re.compile(r"^[\s.,!?;:'\"()\[\]{}\-–—·/+]*$")
_LIST_SEPARATOR_WORD_RE = re.compile(r"(?:와|과|and)", re.IGNORECASE)
_ITEM_INDEX_RE = re.compile(r"\[(\d+)\]")
_TOPIC_PARTICLES = frozenset({"은", "는", "이", "가"})
_KOREAN_SUFFIXES = (
    "이었습니다",
    "였습니다",
    "입니다",
    "됩니다",
    "했습니다",
    "드립니다",
    "이며",
    "이고",
    "으로",
    "에서",
    "까지",
    "부터",
    "처럼",
    "보다",
    "만",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "와",
    "과",
    "도",
    "로",
    "의",
    "에",
)
_SAFE_CONNECTOR_WORDS = frozenset(
    {
        "각각",
        "결과",
        "기준",
        "다음",
        "순서",
        "현재",
        "이며",
        "이고",
        "입니다",
        "됩니다",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "와",
        "과",
        "도",
        "로",
        "의",
        "에",
        "중",
        "the",
        "a",
        "an",
        "and",
        "is",
        "are",
        "was",
        "were",
        "current",
        "currently",
        "with",
        "respectively",
        "result",
        "results",
        "based",
        "on",
    }
)
_LIMITATION_WORDS = frozenset(
    {
        "불확실",
        "확인되지",
        "확인할",
        "알",
        "수",
        "없습니다",
        "제한",
        "추정",
        "unknown",
        "uncertain",
        "unverified",
        "could",
        "not",
        "verify",
        "cannot",
    }
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


def _requested_top_n(question: str) -> int | None:
    for pattern in _TOP_N_PATTERNS:
        match = pattern.search(question)
        if match:
            return int(match.group(1))
    return None


def _item_index(path: str) -> int | None:
    location = _item_location(path)
    return location[1] if location is not None else None


def _item_location(path: str) -> tuple[str, int, str] | None:
    """마지막 concrete list segment의 container/index/field를 반환한다."""
    matches = tuple(_ITEM_INDEX_RE.finditer(path))
    if not matches:
        return None
    match = matches[-1]
    return (
        path[: match.start()],
        int(match.group(1)),
        path[match.end() :].removeprefix("."),
    )


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


def _word_stem(token: str) -> str:
    if not re.fullmatch(r"[가-힣]+", token):
        return token.casefold()
    for suffix in _KOREAN_SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)]
    return token


def _remove_cited_literals(
    content: str,
    cited_values: dict[str, JsonValue],
) -> str:
    literals = sorted(
        {
            item.strip()
            for item in cited_values.values()
            if isinstance(item, str) and item.strip()
        },
        key=len,
        reverse=True,
    )
    residual = content
    for literal in literals:
        residual = re.sub(re.escape(literal), "", residual, flags=re.IGNORECASE)
    return residual


def _blank_spans(content: str, spans: tuple[tuple[int, int], ...]) -> str:
    chars = list(content)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _scope_number_spans(content: str, top_n: int | None) -> set[tuple[int, int]]:
    if top_n is None:
        return set()
    return {
        match.span(1)
        for pattern in _TOP_N_PATTERNS
        for match in pattern.finditer(content)
        if int(match.group(1)) == top_n
    }


def _scope_classifier(
    text: str,
    match: re.Match[str],
) -> tuple[str, tuple[int, int]] | None:
    """top-N 표현 바로 뒤 classifier의 stem과 절대 span을 반환한다."""
    suffix_end = match.end() if match.end() != match.end(1) else len(text)
    classifier = re.match(
        rf"\s*({_WORD_RE.pattern})",
        text[match.end(1) : suffix_end],
    )
    if classifier is None:
        return None
    return (
        _word_stem(classifier.group(1)).casefold(),
        (
            match.end(1) + classifier.start(1),
            match.end(1) + classifier.end(1),
        ),
    )


def _requested_scope_classifier_stems(question: str, top_n: int) -> set[str]:
    """질문의 동일 top-N 표현에 직접 결합된 classifier stem만 수집한다."""
    return {
        classifier[0]
        for pattern in _TOP_N_PATTERNS
        for match in pattern.finditer(question)
        if int(match.group(1)) == top_n
        if (classifier := _scope_classifier(question, match)) is not None
    }


def _scope_phrase_spans(
    content: str,
    *,
    question: str,
    top_n: int | None,
) -> tuple[tuple[int, int], ...]:
    """질문과 같은 requested top-N scope 표현만 lexical fact 검사에서 제외한다."""
    if top_n is None:
        return ()
    requested_classifiers = _requested_scope_classifier_stems(question, top_n)
    spans: list[tuple[int, int]] = []
    for pattern in _TOP_N_PATTERNS:
        for match in pattern.finditer(content):
            if int(match.group(1)) != top_n:
                continue
            spans.append((match.start(), match.end(1)))
            classifier = _scope_classifier(content, match)
            if classifier is not None and classifier[0] in requested_classifiers:
                spans.append(classifier[1])
    return tuple(spans)


def _safe_topic_separator(separator: str) -> bool:
    words = _WORD_RE.findall(separator)
    if words and (len(words) != 1 or words[0] not in _TOPIC_PARTICLES):
        return False
    return _SAFE_PUNCTUATION_RE.fullmatch(_WORD_RE.sub("", separator)) is not None


def _list_boundary_unit(
    separator: str,
    *,
    previous_value: JsonValue,
    question: str,
) -> tuple[str | None, tuple[int, int] | None] | None:
    """목록 경계에서 구두점/열거와 질문에 근거한 compact unit만 허용한다."""
    if _SAFE_PUNCTUATION_RE.fullmatch(_WORD_RE.sub("", separator)) is None:
        return None
    word_matches = tuple(_WORD_RE.finditer(separator))
    if not word_matches:
        return (None, None)

    words = [match.group() for match in word_matches]
    unit_match: re.Match[str] | None = None
    unit = ""
    if len(words) == 1 and words[0].casefold() in {"와", "과", "and"}:
        return (None, None)
    if len(words) == 2 and words[1].casefold() == "and":
        unit_match = word_matches[0]
        unit = words[0]
    elif len(words) == 1:
        unit_match = word_matches[0]
        unit = words[0]
        if len(unit) > 1 and unit[-1] in {"와", "과"}:
            unit = unit[:-1]
    else:
        return None

    if (
        unit_match is None
        or not isinstance(previous_value, int | float)
        or isinstance(previous_value, bool)
        or not unit
        or unit.casefold() in _SAFE_CONNECTOR_WORDS
        or _word_stem(unit).casefold() in _SAFE_CONNECTOR_WORDS
    ):
        return None
    if unit_match.start() != 0 or unit.casefold() not in question.casefold():
        return (None, None)
    return (unit, (unit_match.start(), unit_match.start() + len(unit)))


def _fallback_transition_boundary(
    separator: str,
    *,
    previous_value: JsonValue,
    current_value: JsonValue,
    question: str,
) -> tuple[str | None, tuple[int, int] | None] | None:
    """list 위치가 없는 연속 scalar도 관계 재조합 없이 연결한다."""
    previous_is_number = isinstance(previous_value, int | float) and not isinstance(
        previous_value, bool
    )
    current_is_number = isinstance(current_value, int | float) and not isinstance(
        current_value, bool
    )
    if isinstance(previous_value, str) and current_is_number:
        return (None, None) if _safe_topic_separator(separator) else None
    if previous_is_number and isinstance(current_value, str):
        return _list_boundary_unit(
            separator,
            previous_value=previous_value,
            question=question,
        )
    if (
        isinstance(previous_value, str)
        and isinstance(current_value, str)
        and _safe_topic_separator(separator)
    ):
        return (None, None)
    separator_without_conjunction = _LIST_SEPARATOR_WORD_RE.sub("", separator)
    if _SAFE_PUNCTUATION_RE.fullmatch(separator_without_conjunction) is None:
        return None
    return (None, None)


def _cited_literal_order_error(
    content: str,
    concrete: dict[str, JsonValue],
    cited_values: dict[str, JsonValue],
    *,
    top_n: int | None,
    question: str,
) -> tuple[str | None, tuple[tuple[int, int], ...]]:
    """인용 scalar 순서와 concrete item 관계 shape를 함께 검증한다."""
    cursor = 0
    previous_end: int | None = None
    previous_path: str | None = None
    previous_value: JsonValue = None
    matched_spans: list[tuple[int, int]] = []
    patterns: list[re.Pattern[str]] = []
    boundary_units: list[tuple[str | None, tuple[int, int] | None]] = []
    for path, value in concrete.items():
        if path not in cited_values:
            continue
        pattern = projected_scalar_literal_pattern(value)
        if pattern is None:
            return ("cited_value_order_mismatch", ())
        match = pattern.search(content, cursor)
        if match is None:
            return ("cited_value_order_mismatch", ())
        if previous_end is not None:
            separator = content[previous_end : match.start()]
            if not separator:
                return ("cited_value_order_mismatch", ())
            previous_location = (
                _item_location(previous_path) if previous_path is not None else None
            )
            current_location = _item_location(path)
            if (
                previous_location is not None
                and current_location is not None
                and previous_location[:2] == current_location[:2]
            ):
                if not isinstance(previous_value, str) or not _safe_topic_separator(
                    separator
                ):
                    return ("cited_value_order_mismatch", ())
            elif (
                previous_location is not None
                and current_location is not None
                and previous_location[0] == current_location[0]
                and previous_location[1] != current_location[1]
            ):
                boundary = _list_boundary_unit(
                    separator,
                    previous_value=previous_value,
                    question=question,
                )
                if boundary is None:
                    return ("cited_value_order_mismatch", ())
                unit, relative_span = boundary
                absolute_span = None
                if relative_span is not None:
                    absolute_span = (
                        previous_end + relative_span[0],
                        previous_end + relative_span[1],
                    )
                boundary_units.append((unit, absolute_span))
            else:
                boundary = _fallback_transition_boundary(
                    separator,
                    previous_value=previous_value,
                    current_value=value,
                    question=question,
                )
                if boundary is None:
                    return ("cited_value_order_mismatch", ())
                unit, relative_span = boundary
                absolute_span = None
                if relative_span is not None:
                    absolute_span = (
                        previous_end + relative_span[0],
                        previous_end + relative_span[1],
                    )
                boundary_units.append((unit, absolute_span))
        cursor = match.end()
        previous_end = match.end()
        previous_path = path
        previous_value = value
        matched_spans.append(match.span())
        patterns.append(pattern)
    if previous_end is None:
        return ("cited_value_order_mismatch", ())

    allowed_unit_spans: list[tuple[int, int]] = []
    rendered_units = [unit for unit, _ in boundary_units if unit is not None]
    if rendered_units:
        expected_unit = rendered_units[0]
        units_are_consistent = len(rendered_units) == len(boundary_units) and all(
            unit == expected_unit for unit in rendered_units
        )
        tail = content[previous_end:]
        if units_are_consistent and tail.startswith(expected_unit):
            allowed_unit_spans.extend(
                span for _, span in boundary_units if span is not None
            )
            allowed_unit_spans.append(
                (previous_end, previous_end + len(expected_unit))
            )

    unmatched_chars = list(content)
    for start, end in matched_spans:
        unmatched_chars[start:end] = " " * (end - start)
    unmatched = "".join(unmatched_chars)
    if any(pattern.search(unmatched) for pattern in patterns):
        return ("cited_value_order_mismatch", ())
    allowed_scope_spans = _scope_number_spans(content, top_n)
    unmatched_without_urls = _URL_RE.sub(
        lambda match: " " * (match.end() - match.start()),
        unmatched,
    )
    for match in _NUMBER_RE.finditer(unmatched_without_urls):
        if match.span() not in allowed_scope_spans:
            return ("ungrounded_number", ())
    return (None, tuple(allowed_unit_spans))


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
    if _EMAIL_RE.search(content):
        return _rejected("private_or_unprojected_identifier")

    concrete = flatten_public_facts(value.public_facts)
    if not draft.cited_paths:
        return _rejected("citations_required")
    if len(draft.cited_paths) != len(set(draft.cited_paths)):
        return _rejected("duplicate_citation")
    top_n = _requested_top_n(value.question)
    cited_values: dict[str, JsonValue] = {}
    cited_item_indices: set[int] = set()
    cited_item_string_indices: set[int] = set()
    for path in draft.cited_paths:
        if "[*]" in path or path not in concrete:
            return _rejected("citation_not_projected")
        index = _item_index(path)
        if top_n is not None:
            if index is not None and index >= top_n:
                return _rejected("citation_outside_requested_scope")
            if index is not None:
                cited_item_indices.add(index)
        cited = concrete[path]
        cited_values[path] = cited
        if index is not None and isinstance(cited, str) and cited.strip():
            cited_item_string_indices.add(index)
        if (
            isinstance(cited, str)
            and len(cited.strip()) >= 2
            and not projected_scalar_is_visible(content, cited)
        ):
            return _rejected("cited_value_not_rendered")
    if top_n is not None and cited_item_indices != _required_item_indices(
        concrete, top_n
    ):
        return _rejected("requested_scope_not_fully_cited")
    if top_n is not None and cited_item_string_indices != _required_item_indices(
        concrete, top_n
    ):
        return _rejected("requested_item_identity_not_cited")
    literal_order_error, allowed_unit_spans = _cited_literal_order_error(
        content,
        concrete,
        cited_values,
        top_n=top_n,
        question=value.question,
    )
    if literal_order_error is not None:
        return _rejected(literal_order_error)

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
            and not any(
                cited_value == projected
                for cited_value in cited_values.values()
            )
        ):
            return _rejected("rendered_value_not_cited")

    allowed_lexical_spans = allowed_unit_spans + _scope_phrase_spans(
        content,
        question=value.question,
        top_n=top_n,
    )
    lexical_residual = _remove_cited_literals(
        _blank_spans(content, allowed_lexical_spans),
        cited_values,
    )
    allowed_words = _SAFE_CONNECTOR_WORDS
    if value.unresolved_claims:
        allowed_words = allowed_words | _LIMITATION_WORDS
    for token in _WORD_RE.findall(_URL_RE.sub("", lexical_residual)):
        folded = token.casefold()
        stem = _word_stem(token).casefold()
        if (
            folded not in allowed_words
            and stem not in allowed_words
        ):
            return _rejected("ungrounded_text")
    symbol_residual = _WORD_RE.sub(
        "",
        _NUMBER_RE.sub("", _URL_RE.sub("", lexical_residual)),
    )
    if not _SAFE_PUNCTUATION_RE.fullmatch(symbol_residual):
        return _rejected("ungrounded_symbol")

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
    allowed_numbers: set[str] = set()
    if top_n is not None:
        allowed_numbers.add(str(top_n))
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
