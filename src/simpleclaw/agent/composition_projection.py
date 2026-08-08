"""Asset contract allowlist만 따라 bounded composer fact projection을 만든다."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from simpleclaw.graph_runtime.contracts import (
    ContractDescriptorV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus

from .composition_citations import projected_scalar_literal_pattern
from .composition_contracts import (
    CompositionInputV1,
    StructuralEvidenceRelationV1,
)

MAX_COMPOSITION_ARRAY_ITEMS = 20
MAX_COMPOSITION_SERIALIZED_CHARS = 12_000
MAX_COMPOSITION_SCALAR_CHARS = 2_000
_MISSING = object()
_CONCRETE_INDEX_RE = re.compile(r"\[(\d+)\]")


class CompositionProjectionError(ValueError):
    """Projection이 안전하게 구성될 수 없음을 stable code로 알린다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _validate_value(value: Any) -> None:
    """Composer-visible 값에 non-JSON·비정상 수·과대 scalar가 없게 한다."""
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CompositionProjectionError("projection.non_finite_number")
        return
    if isinstance(value, str):
        if len(value) > MAX_COMPOSITION_SCALAR_CHARS:
            raise CompositionProjectionError("projection.scalar_too_long")
        return
    if isinstance(value, list):
        if len(value) > MAX_COMPOSITION_ARRAY_ITEMS:
            raise CompositionProjectionError("projection.array_too_large")
        for item in value:
            _validate_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CompositionProjectionError("projection.non_string_key")
            _validate_value(item)
        return
    raise CompositionProjectionError("projection.non_json_value")


def _path_tokens(path: str) -> tuple[tuple[str, bool], ...]:
    return tuple(
        (segment.removesuffix("[*]"), segment.endswith("[*]"))
        for segment in path.split(".")
    )


def _fragment(value: object, tokens: Sequence[tuple[str, bool]]) -> object:
    """선언 path 하나를 원래 index 구조를 보존하는 nested fragment로 만든다."""
    if not tokens:
        _validate_value(value)
        return copy.deepcopy(value)
    key, wildcard = tokens[0]
    if not isinstance(value, Mapping) or key not in value:
        return _MISSING
    child = value[key]
    if wildcard:
        if not isinstance(child, list):
            raise CompositionProjectionError("projection.wildcard_not_array")
        if len(child) > MAX_COMPOSITION_ARRAY_ITEMS:
            raise CompositionProjectionError("projection.array_too_large")
        projected: list[object] = []
        matched = False
        for item in child:
            item_fragment = _fragment(item, tokens[1:])
            if item_fragment is _MISSING:
                projected.append({})
            else:
                matched = True
                projected.append(item_fragment)
        if not matched and child:
            return _MISSING
        return {key: projected}
    nested = _fragment(child, tokens[1:])
    return _MISSING if nested is _MISSING else {key: nested}


def _merge(left: object, right: object) -> object:
    """동일 source shape에서 만든 fragments만 결정적으로 병합한다."""
    if isinstance(left, dict) and isinstance(right, dict):
        merged = copy.deepcopy(left)
        for key, value in right.items():
            merged[key] = _merge(merged[key], value) if key in merged else copy.deepcopy(value)
        return merged
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [_merge(a, b) for a, b in zip(left, right, strict=True)]
    if left == right:
        return copy.deepcopy(left)
    raise CompositionProjectionError("projection.path_conflict")


def project_declared_paths(
    payload: Mapping[str, object],
    paths: Sequence[str],
) -> dict[str, JsonValue]:
    """선언된 path가 실제로 존재하는 값만 원래 구조로 투영한다."""
    projected: object = {}
    matched = 0
    for path in paths:
        value = _fragment(payload, _path_tokens(path))
        if value is _MISSING:
            continue
        projected = _merge(projected, value)
        matched += 1
    if matched == 0 or not isinstance(projected, dict):
        raise CompositionProjectionError("projection.empty")
    encoded = json.dumps(
        projected,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded) > MAX_COMPOSITION_SERIALIZED_CHARS:
        raise CompositionProjectionError("projection.too_large")
    return json.loads(encoded)


def _claims(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Malformed claim metadata가 composer input으로 암묵 승격되지 않게 한다."""
    value = payload.get(key, ())
    if value in (None, ()):
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or len(item) > 300
        for item in value
    ):
        raise CompositionProjectionError(f"projection.invalid_{key}")
    return tuple(item.strip() for item in value)


def _concrete_path_pattern(path: str) -> re.Pattern[str]:
    escaped = re.escape(path).replace(r"\[\*\]", r"\[\d+\]")
    return re.compile(rf"^{escaped}$")


def _expand_structural_field(
    value: object,
    tokens: Sequence[tuple[str, bool]],
    *,
    prefix: str = "",
) -> tuple[tuple[str, ...], bool]:
    """Wildcard source item마다 required field가 존재하는지 함께 확장한다."""
    if not tokens:
        return ((prefix,), True)
    key, wildcard = tokens[0]
    if not isinstance(value, Mapping) or key not in value:
        return ((), False)
    child = value[key]
    path = f"{prefix}.{key}" if prefix else key
    if not wildcard:
        return _expand_structural_field(child, tokens[1:], prefix=path)
    if not isinstance(child, list):
        return ((), False)
    expanded: list[str] = []
    for index, item in enumerate(child):
        matches, complete = _expand_structural_field(
            item,
            tokens[1:],
            prefix=f"{path}[{index}]",
        )
        if not complete:
            return ((), False)
        expanded.extend(matches)
    return (tuple(expanded), True)


def _evidence_path_order(
    path: str,
    *,
    patterns: tuple[re.Pattern[str], ...],
    concrete_order: dict[str, int],
) -> tuple[tuple[int, ...], int, int]:
    """List index를 먼저, descriptor field 순서를 다음으로 보존한다."""
    indices = tuple(int(value) for value in _CONCRETE_INDEX_RE.findall(path))
    pattern_index = next(
        index for index, pattern in enumerate(patterns) if pattern.fullmatch(path)
    )
    return (indices, pattern_index, concrete_order[path])


def _structural_evidence_relations(
    public_facts: Mapping[str, JsonValue],
    descriptor: ContractDescriptorV1,
) -> tuple[StructuralEvidenceRelationV1, ...]:
    """활성 relation의 모든 evidence를 source index 순서로 구체화한다."""
    concrete = flatten_public_facts(public_facts)
    activated: list[StructuralEvidenceRelationV1] = []
    for declaration in descriptor.structural_evidence_relations:
        if concrete.get(declaration.when_path, _MISSING) != declaration.when_equals:
            continue
        patterns = tuple(
            _concrete_path_pattern(field) for field in declaration.evidence_fields
        )
        expanded_fields = tuple(
            _expand_structural_field(
                public_facts,
                _path_tokens(field),
            )
            for field in declaration.evidence_fields
        )
        if any(not complete for _, complete in expanded_fields):
            raise CompositionProjectionError(
                "projection.structural_evidence_incomplete"
            )
        matches_by_pattern = tuple(paths for paths, _ in expanded_fields)
        if any(
            path not in concrete or isinstance(concrete[path], dict | list)
            for paths in matches_by_pattern
            for path in paths
        ):
            raise CompositionProjectionError(
                "projection.structural_evidence_not_scalar"
            )
        if any(
            projected_scalar_literal_pattern(concrete[path]) is None
            for paths in matches_by_pattern
            for path in paths
        ):
            raise CompositionProjectionError(
                "projection.structural_evidence_not_renderable"
            )
        if any(not matches for matches in matches_by_pattern):
            continue
        concrete_order = {path: index for index, path in enumerate(concrete)}
        evidence_paths = tuple(
            sorted(
                {
                    path
                    for matches in matches_by_pattern
                    for path in matches
                },
                key=lambda path: _evidence_path_order(
                    path,
                    patterns=patterns,
                    concrete_order=concrete_order,
                ),
            )
        )
        if evidence_paths:
            identity_patterns = tuple(
                _concrete_path_pattern(field)
                for field in declaration.identity_fields
            )
            identity_paths = tuple(
                path
                for path in evidence_paths
                if any(pattern.fullmatch(path) for pattern in identity_patterns)
            )
            activated.append(
                StructuralEvidenceRelationV1(
                    evidence_paths=evidence_paths,
                    identity_paths=identity_paths,
                )
            )
    return tuple(activated)


def build_composition_input(
    *,
    request_id: str,
    question: str,
    locale: str,
    selected_route: str,
    normalized_result: NormalizedAssetResultV1,
    descriptor: ContractDescriptorV1,
) -> CompositionInputV1:
    """Safe result와 exact descriptor에서 중앙 composer 입력을 만든다."""
    if normalized_result.status is not AssetResultStatus.RESOLVED:
        raise CompositionProjectionError("projection.result_not_resolved")
    if normalized_result.effect_status not in {EffectStatus.NONE, EffectStatus.VERIFIED}:
        raise CompositionProjectionError("projection.effect_not_safe")
    if descriptor.ref != normalized_result.output_contract:
        raise CompositionProjectionError("projection.contract_mismatch")
    payload = normalized_result.payload
    if payload.get("side_effect") is not False:
        raise CompositionProjectionError("projection.payload_effect_not_safe")
    if not descriptor.composition_fields:
        raise CompositionProjectionError("projection.fields_not_declared")
    public_facts = project_declared_paths(payload, descriptor.composition_fields)
    return CompositionInputV1(
        request_id=request_id,
        question=question,
        locale=locale,
        selected_route=selected_route,
        asset_ref=descriptor.ref.owner_ref,
        result_status=normalized_result.status,
        effect_status=normalized_result.effect_status,
        normalized_payload_hash=normalized_result.payload_hash,
        public_facts=public_facts,
        resolved_claims=_claims(payload, "resolved_claims"),
        unresolved_claims=_claims(payload, "unresolved_claims"),
        structural_evidence_relations=_structural_evidence_relations(
            public_facts,
            descriptor,
        ),
    )


def flatten_public_facts(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Guard citation 검사용 concrete indexed scalar path map을 만든다."""
    flattened: dict[str, JsonValue] = {}

    def visit(node: JsonValue, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                visit(child, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            if not node:
                flattened[path] = node
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")
        else:
            flattened[path] = node

    visit(dict(value), "")
    return flattened
