import pytest

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


@pytest.mark.parametrize(
    ("fresh", "allow_final", "limitation"),
    [
        (None, False, "unknown_freshness:score"),
        (False, False, "stale_evidence:score"),
        (True, True, None),
    ],
)
def test_required_current_claim_freshness_is_fail_closed(
    fresh: bool | None,
    allow_final: bool,
    limitation: str | None,
) -> None:
    ledger = ResolutionLedger(
        evidence=[
            EvidenceRecord(
                claim_id="score",
                value="70",
                source_url="https://example.test/score",
                fresh=fresh,
            )
        ]
    )
    decision = CommonResultValidator().validate(
        goal=GoalResolutionState(
            original_goal="current score",
            status=GoalStatus.RESOLVED,
            resolved_claims=("score",),
            unresolved_claims=(),
        ),
        ledger=ledger,
        required_claims=("score",),
    )

    assert decision.allow_final is allow_final
    if limitation is not None:
        assert limitation in decision.limitations


def test_typed_policy_can_mark_claim_freshness_optional() -> None:
    ledger = ResolutionLedger(
        evidence=[
            EvidenceRecord(
                claim_id="definition",
                value="a meaning",
                source_url="https://example.test/definition",
                fresh=None,
            )
        ]
    )
    decision = CommonResultValidator().validate(
        goal=GoalResolutionState(
            original_goal="define it",
            status=GoalStatus.RESOLVED,
            resolved_claims=("definition",),
            unresolved_claims=(),
        ),
        ledger=ledger,
        required_claims=("definition",),
        freshness_optional_claims=("definition",),
    )

    assert decision.allow_final is True
