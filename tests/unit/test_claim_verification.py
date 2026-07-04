from simpleclaw.agent.claim_verification import verify_answer_claims
from simpleclaw.agent.fact_types import EvidenceCoverage, EvidenceItem, EvidenceSlot, FactPlan


def test_claim_verifier_allows_answer_with_evidence_terms():
    slot = EvidenceSlot(name="current_state", question="현재 상태?")
    slot.add_evidence(EvidenceItem(
        source_url="https://official.example",
        claim="한국은 조 3위다",
        coverage=EvidenceCoverage.FINAL,
        confidence="high",
    ))
    plan = FactPlan(task_type="scenario_analysis", complexity_score=4, slots=[slot])

    result = verify_answer_claims("확인한 근거상 한국은 조 3위입니다.", plan)

    assert result.allow_final is True


def test_claim_verifier_flags_missing_source_when_no_slots_filled():
    plan = FactPlan(task_type="scenario_analysis", complexity_score=4, slots=[])

    result = verify_answer_claims("한국은 확정 진출입니다.", plan)

    assert result.allow_final is False
    assert result.unsupported_reasons
