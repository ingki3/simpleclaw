from simpleclaw.agent.fact_types import (
    EvidenceCoverage,
    EvidenceItem,
    EvidenceSlot,
    FactPlan,
    SlotStatus,
)


def test_fact_plan_reports_missing_required_slots():
    plan = FactPlan(
        task_type="scenario_analysis",
        complexity_score=5,
        slots=[
            EvidenceSlot(name="current_state", question="현재 상태?", required=True),
            EvidenceSlot(name="rules", question="규칙?", required=True),
        ],
        requires_calculation=True,
        max_iterations=4,
    )

    assert [slot.name for slot in plan.missing_required_slots()] == [
        "current_state",
        "rules",
    ]
    assert not plan.required_slots_complete()


def test_slot_accepts_final_evidence():
    slot = EvidenceSlot(name="current_state", question="현재 상태?", required=True)
    evidence = EvidenceItem(
        source_url="https://example.com/official",
        source_title="Official",
        source_type="official",
        claim="현재 상태는 X다",
        extracted_value="X",
        coverage=EvidenceCoverage.FINAL,
        confidence="high",
    )

    slot.add_evidence(evidence)

    assert slot.status == SlotStatus.FILLED
    assert slot.evidence == [evidence]
