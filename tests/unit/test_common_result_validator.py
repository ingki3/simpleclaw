from simpleclaw.agent.resolution_ledger import EvidenceRecord, ResolutionLedger
from simpleclaw.agent.resolution_types import GoalResolutionState, GoalStatus
from simpleclaw.agent.result_validator import CommonResultValidator


def test_validator_requires_provenance_for_required_claim() -> None:
    ledger = ResolutionLedger(
        evidence=[EvidenceRecord(claim_id="score", value="70", usable=True)]
    )
    decision = CommonResultValidator().validate(
        goal=GoalResolutionState(
            original_goal="score",
            status=GoalStatus.RESOLVED,
            resolved_claims=(),
            unresolved_claims=(),
        ),
        ledger=ledger,
        required_claims=("score",),
    )
    assert decision.allow_final is False
    assert decision.blocked_claims == ("score",)

