"""Provider typed claim map을 exact asset evidence로 검증·정규화한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _validated_claim_map(observation: object) -> dict[str, dict[str, Any]]:
    """출처·시각·freshness가 완전한 provider claim만 반환한다."""
    if not isinstance(observation, Mapping):
        return {}
    raw_claims = observation.get("claim_map")
    if not isinstance(raw_claims, Mapping):
        return {}

    validated: dict[str, dict[str, Any]] = {}
    for raw_key, raw_record in raw_claims.items():
        key = str(raw_key).strip()
        if not key or not isinstance(raw_record, Mapping) or "value" not in raw_record:
            continue
        source_url = str(raw_record.get("source_url") or "").strip()
        observed_at = str(
            raw_record.get("observed_at") or raw_record.get("fetched_at") or ""
        ).strip()
        if (
            not source_url
            or not observed_at
            or raw_record.get("fresh") is not True
            or raw_record.get("usable", True) is not True
        ):
            continue
        validated[key] = {
            "value": raw_record["value"],
            "source_url": source_url,
            "provenance": str(raw_record.get("provenance") or "").strip(),
            "observed_at": observed_at,
            "fresh": True,
            "usable": True,
        }
    return validated


def materialize_validated_claims(
    observation: object,
    *,
    required_claims: Sequence[str],
    claim_bindings: Mapping[str, Sequence[str]],
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """검증된 provider claim key에 명시적으로 bind된 claim만 해결한다."""
    required = tuple(
        dict.fromkeys(
            str(item).strip()
            for item in required_claims
            if str(item).strip()
        )
    )
    claim_map = _validated_claim_map(observation)
    resolved: list[str] = []
    evidence: list[dict[str, Any]] = []

    for claim_id in required:
        keys = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in claim_bindings.get(claim_id, ())
                if str(item).strip()
            )
        )
        records = [claim_map[key] for key in keys if key in claim_map]
        if not keys or len(records) != len(keys):
            continue
        source_urls = {record["source_url"] for record in records}
        observed_times = {record["observed_at"] for record in records}
        if len(source_urls) != 1 or len(observed_times) != 1:
            continue
        value = (
            records[0]["value"]
            if len(records) == 1
            else {
                key: record["value"]
                for key, record in zip(keys, records, strict=True)
            }
        )
        resolved.append(claim_id)
        evidence.append(
            {
                "claim_id": claim_id,
                "value": value,
                "source_url": records[0]["source_url"],
                "provenance": records[0]["provenance"],
                "observed_at": records[0]["observed_at"],
                "fresh": True,
                "usable": True,
            }
        )

    unresolved = [claim for claim in required if claim not in resolved]
    return resolved, unresolved, evidence


def declared_claim_bindings(
    *,
    required_claims: Sequence[str],
    declared_resolved_claims: object,
    declared_evidence: object,
) -> dict[str, tuple[str, ...]]:
    """Recipe envelope가 명시한 claim→provider claim key binding만 추출한다."""
    required = frozenset(str(item).strip() for item in required_claims)
    resolved = (
        frozenset(str(item).strip() for item in declared_resolved_claims)
        if isinstance(declared_resolved_claims, list | tuple)
        else frozenset()
    )
    bindings: dict[str, tuple[str, ...]] = {}
    if not isinstance(declared_evidence, list | tuple):
        return bindings
    for item in declared_evidence:
        if not isinstance(item, Mapping):
            continue
        claim_id = str(item.get("claim_id") or "").strip()
        raw_keys = item.get("claim_keys")
        if (
            claim_id not in required
            or claim_id not in resolved
            or not isinstance(raw_keys, list | tuple)
        ):
            continue
        keys = tuple(
            dict.fromkeys(
                str(key).strip() for key in raw_keys if str(key).strip()
            )
        )
        if keys:
            bindings[claim_id] = keys
    return bindings
