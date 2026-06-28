"""Lightweight final-answer claim guard for complex fact workflow."""

from __future__ import annotations

from dataclasses import dataclass, field

from simpleclaw.agent.fact_types import FactPlan

_CERTAINTY_MARKERS = ("확정", "무조건", "최종", "반드시", "진출입니다", "탈락입니다")


@dataclass(frozen=True)
class ClaimVerificationResult:
    allow_final: bool
    unsupported_reasons: list[str] = field(default_factory=list)


def verify_answer_claims(answer: str, plan: FactPlan) -> ClaimVerificationResult:
    """Conservatively block certainty when evidence slots are missing."""

    if not plan.slots or not any(slot.evidence for slot in plan.slots):
        return ClaimVerificationResult(False, ["no_evidence_slots"])
    missing = plan.missing_required_slots()
    if missing and any(marker in answer for marker in _CERTAINTY_MARKERS):
        return ClaimVerificationResult(
            False,
            ["certainty_claim_with_missing_required_slots"],
        )
    return ClaimVerificationResult(True)
