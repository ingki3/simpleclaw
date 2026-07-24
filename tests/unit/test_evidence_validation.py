from simpleclaw.agent.evidence_validation import validate_slot_evidence
from simpleclaw.agent.fact_types import (
    EvidenceCoverage,
    EvidenceItem,
    EvidenceSlot,
    SlotStatus,
)


def test_required_fresh_slot_rejects_pre_event_evidence():
    slot = EvidenceSlot(name="current_state", question="현재 상태?", freshness_required=True)
    item = EvidenceItem(
        source_url="https://example.com/preview",
        source_type="major_news",
        claim="경기 예정",
        coverage=EvidenceCoverage.PRE_EVENT,
        confidence="high",
    )

    result = validate_slot_evidence(slot, [item])

    assert result.status == SlotStatus.STALE
    assert "stale_or_pre_event" in " ".join(result.limitations)


def test_official_final_evidence_fills_slot():
    slot = EvidenceSlot(
        name="decision_rules",
        question="규칙?",
        freshness_required=False,
        preferred_source_type="official",
    )
    item = EvidenceItem(
        source_url="https://official.example/rules",
        source_type="official",
        claim="상위 2팀 진출",
        coverage=EvidenceCoverage.FINAL,
        confidence="high",
    )

    result = validate_slot_evidence(slot, [item])

    assert result.status == SlotStatus.FILLED
    assert result.evidence == [item]
