"""Compatibility import for the canonical typed evidence state.

BIZ-520 introduced the provider-neutral evidence policy.  BIZ-523 extends that
same model instead of creating a second status hierarchy.
"""

from simpleclaw.agent.evidence_policy import (
    EvidenceFreshness,
    EvidenceSourceType,
    EvidenceState,
    EvidenceStatus,
)

__all__ = [
    "EvidenceFreshness",
    "EvidenceSourceType",
    "EvidenceState",
    "EvidenceStatus",
]
