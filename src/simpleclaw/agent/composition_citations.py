"""Composer citation metadata의 deterministic visibility canonicalization."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from .composition_contracts import CompositionInputV1, DraftResponseV1
from .composition_projection import flatten_public_facts

CITATION_CANONICALIZATION_POLICY_VERSION = "visible_subset_v1"


def projected_scalar_literal_pattern(
    value: JsonValue,
) -> re.Pattern[str] | None:
    """Guard와 canonicalizer가 공유하는 exact-visible scalar pattern이다."""
    if value is None or isinstance(value, dict | list):
        return None
    rendered = str(value).strip()
    if not rendered:
        return None
    escaped = re.escape(rendered)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return re.compile(rf"(?<![\d.+%-]){escaped}(?![\d.+%-])")
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
    """본문 불변으로 provider citation의 safe visible subset만 반영한다."""
    concrete = flatten_public_facts(value.public_facts)
    provider_paths = tuple(draft.cited_paths)
    if len(provider_paths) == len(set(provider_paths)):
        selected = set(provider_paths)
        for relation in value.semantic_relations:
            if (
                relation.kind == "question_scope_absent"
                and set(relation.evidence_paths) <= selected
                and all(path in concrete for path in relation.evidence_paths)
            ):
                return draft.model_copy(
                    update={"cited_paths": relation.evidence_paths}
                )
    cited_paths = canonical_visible_cited_paths(
        draft.content,
        provider_paths,
        concrete,
    )
    if not cited_paths or cited_paths == draft.cited_paths:
        return draft
    return draft.model_copy(update={"cited_paths": cited_paths})
