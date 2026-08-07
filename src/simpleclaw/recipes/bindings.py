"""Recipe-owned query constraints를 결정적 delegate argv 계약으로 해석한다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simpleclaw.capability import OwnedBindingMetadata
from simpleclaw.recipes.models import RecipeDefinition


@dataclass(frozen=True)
class ResolvedDelegateArgumentConstraint:
    """Recipe binding에서 해석한 단일 delegate tool argument 제약."""

    name: str
    target_skill: str
    argument_name: str
    flag: str
    value: int


def resolve_step_argument_constraints(
    metadata: OwnedBindingMetadata,
    payload: Mapping[str, Any],
) -> tuple[ResolvedDelegateArgumentConstraint, ...]:
    """한 step binding의 literal marker 기반 bounded integer를 해석한다."""
    binding = metadata.binding
    target = binding.get("target_skill")
    raw_constraints = binding.get("argument_constraints", [])
    if not isinstance(target, str) or not target:
        raise ValueError("target_skill must be a non-empty string")
    if not isinstance(raw_constraints, list):
        raise TypeError("argument_constraints must be a list")

    resolved: list[ResolvedDelegateArgumentConstraint] = []
    names: set[str] = set()
    for raw in raw_constraints:
        if not isinstance(raw, dict):
            raise TypeError("argument constraint must be an object")
        allowed = {
            "name",
            "source",
            "strategy",
            "prefixes",
            "prefix_optional",
            "suffixes",
            "default",
            "minimum",
            "maximum",
            "argument_name",
            "flag",
        }
        if set(raw) - allowed:
            raise ValueError("argument constraint contains unsupported keys")
        name = _required_string(raw.get("name"), "constraint name")
        source = _required_string(raw.get("source"), "constraint source")
        strategy = _required_string(raw.get("strategy"), "constraint strategy")
        argument_name = _required_string(
            raw.get("argument_name"), "constraint argument_name"
        )
        flag = _required_string(raw.get("flag"), "constraint flag")
        if name in names:
            raise ValueError("argument constraint names must be unique")
        if strategy != "bounded_integer_between_markers":
            raise ValueError("unsupported argument constraint strategy")
        if source not in payload or not isinstance(payload[source], str):
            raise ValueError("argument constraint source must be a string payload field")

        minimum = _integer(raw.get("minimum"), "constraint minimum")
        maximum = _integer(raw.get("maximum"), "constraint maximum")
        default = _integer(raw.get("default"), "constraint default")
        if minimum > maximum or not minimum <= default <= maximum:
            raise ValueError("argument constraint bounds/default are invalid")
        prefixes = _string_list(raw.get("prefixes"), "constraint prefixes")
        suffixes = _string_list(raw.get("suffixes"), "constraint suffixes")
        prefix_optional = raw.get("prefix_optional", False)
        if not isinstance(prefix_optional, bool):
            raise TypeError("constraint prefix_optional must be a boolean")
        if not prefixes or not suffixes:
            raise ValueError("argument constraint markers must not be empty")

        matches = _bounded_integer_matches(
            payload[source],
            prefixes=prefixes,
            suffixes=suffixes,
            prefix_optional=prefix_optional,
        )
        distinct = tuple(dict.fromkeys(matches))
        if len(distinct) > 1:
            raise ValueError("argument constraint is ambiguous")
        value = distinct[0] if distinct else default
        if not minimum <= value <= maximum:
            raise ValueError("argument constraint is outside the declared bounds")
        names.add(name)
        resolved.append(
            ResolvedDelegateArgumentConstraint(
                name=name,
                target_skill=target,
                argument_name=argument_name,
                flag=flag,
                value=value,
            )
        )
    return tuple(resolved)


def resolve_recipe_argument_constraints(
    recipe: RecipeDefinition,
    payload: Mapping[str, Any],
) -> tuple[ResolvedDelegateArgumentConstraint, ...]:
    """Recipe의 모든 step constraint를 선언 순서대로 해석한다."""
    resolved = tuple(
        item
        for metadata in recipe.step_bindings
        for item in resolve_step_argument_constraints(metadata, payload)
    )
    identities = {(item.target_skill, item.name) for item in resolved}
    if len(identities) != len(resolved):
        raise ValueError("recipe argument constraint identities must be unique")
    return resolved


def constraint_values(
    constraints: tuple[ResolvedDelegateArgumentConstraint, ...],
) -> dict[str, int]:
    """Bound payload sidecar와 render 변수가 공유할 canonical name/value를 만든다."""
    values: dict[str, int] = {}
    for item in constraints:
        if item.name in values and values[item.name] != item.value:
            raise ValueError("recipe argument constraint values conflict")
        values[item.name] = item.value
    return values


def _bounded_integer_matches(
    text: str,
    *,
    prefixes: tuple[str, ...],
    suffixes: tuple[str, ...],
    prefix_optional: bool,
) -> tuple[int, ...]:
    """Asset이 선언한 literal marker 사이의 ASCII integer만 추출한다."""
    matches: list[tuple[int, int]] = []
    ordered_prefixes = sorted(prefixes, key=len, reverse=True)
    if prefix_optional:
        ordered_prefixes.append("")
    ordered_suffixes = sorted(suffixes, key=len, reverse=True)
    for prefix in ordered_prefixes:
        prefix_pattern = re.escape(prefix) if prefix else r"(?<![0-9])"
        for suffix in ordered_suffixes:
            suffix_pattern = re.escape(suffix)
            pattern = re.compile(
                rf"{prefix_pattern}\s*([0-9]{{1,4}})\s*{suffix_pattern}",
                flags=re.IGNORECASE,
            )
            for match in pattern.finditer(text):
                matches.append((match.start(1), int(match.group(1))))
    return tuple(value for _offset, value in sorted(set(matches)))


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{label} must be a string list")
    normalized = tuple(dict.fromkeys(item.strip() for item in value))
    if any(not item for item in normalized):
        raise ValueError(f"{label} must not contain empty markers")
    return normalized


__all__ = [
    "ResolvedDelegateArgumentConstraint",
    "constraint_values",
    "resolve_recipe_argument_constraints",
    "resolve_step_argument_constraints",
]
