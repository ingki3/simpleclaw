"""Evidence validation helpers for complex factual workflows."""

from __future__ import annotations

from collections import defaultdict

from simpleclaw.agent.fact_types import (
    EvidenceCoverage,
    EvidenceItem,
    EvidenceSlot,
    SlotStatus,
)

_FINALISH = {EvidenceCoverage.FINAL, EvidenceCoverage.CURRENT_PENDING}
_STALEISH = {EvidenceCoverage.PRE_EVENT, EvidenceCoverage.STALE}
_SOURCE_RANK = {
    "official": 4,
    "major_news": 3,
    "specialist": 2,
    "blog": 1,
    "unknown": 0,
}
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def validate_slot_evidence(slot: EvidenceSlot, candidates: list[EvidenceItem]) -> EvidenceSlot:
    """Validate candidate evidence and return a new slot with status/limitations."""

    validated = EvidenceSlot(
        name=slot.name,
        question=slot.question,
        required=slot.required,
        freshness_required=slot.freshness_required,
        preferred_source_type=slot.preferred_source_type,
        depends_on=list(slot.depends_on),
    )

    if not candidates:
        validated.status = SlotStatus.MISSING
        validated.limitations.append("no_evidence")
        return validated

    conflicts = _detect_conflicts(candidates)
    usable = [item for item in candidates if item.coverage in _FINALISH]
    stale = [item for item in candidates if item.coverage in _STALEISH]

    if slot.freshness_required and stale and not usable:
        validated.status = SlotStatus.STALE
        validated.evidence = _rank_items(stale)
        validated.limitations.append("only stale_or_pre_event evidence available")
        return validated

    if conflicts and len(usable) > 1:
        validated.status = SlotStatus.CONFLICTING
        validated.evidence = _rank_items(usable)
        validated.limitations.append("conflicting extracted values across sources")
        return validated

    if usable:
        ranked = _rank_items(usable)
        preferred = [
            item for item in ranked
            if slot.preferred_source_type is None or item.source_type == slot.preferred_source_type
        ]
        selected = (preferred or ranked)[:3]
        for item in selected:
            validated.add_evidence(item)
        if any(item.coverage == EvidenceCoverage.CURRENT_PENDING for item in selected):
            validated.limitations.append(
                "current_pending: answer must separate confirmed and pending parts"
            )
        return validated

    partial = [item for item in candidates if item.coverage == EvidenceCoverage.PARTIAL]
    if partial:
        validated.status = SlotStatus.PARTIAL
        validated.evidence = _rank_items(partial)
        validated.limitations.append("partial_coverage")
        return validated

    validated.status = SlotStatus.PARTIAL
    validated.evidence = _rank_items(candidates)
    validated.limitations.append("unknown_coverage")
    return validated


def _rank_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(
        items,
        key=lambda item: (
            _SOURCE_RANK.get(item.source_type, 0),
            _CONFIDENCE_RANK.get(item.confidence, 0),
        ),
        reverse=True,
    )


def _detect_conflicts(items: list[EvidenceItem]) -> bool:
    values: dict[str, set[str]] = defaultdict(set)
    for item in items:
        if item.extracted_value:
            values[item.claim.strip().lower()].add(str(item.extracted_value).strip().lower())
    return any(len(v) > 1 for v in values.values())
