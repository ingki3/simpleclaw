"""Capability metadata가 routing surface로만 남는 계약 테스트."""

from __future__ import annotations

import simpleclaw.capability as capability_module
from simpleclaw.capability import CapabilityMetadata, parse_capability_metadata


def test_capability_metadata_remains_available_for_routing():
    capability = parse_capability_metadata(
        {
            "domains": ["market"],
            "intents": ["quote"],
            "read_only": True,
            "side_effects": False,
            "freshness_sensitive": True,
            "direct_answer": True,
            "requires_confirmation": False,
            "output_contract": "narrative_context",
        },
        source="fixture/SKILL.md",
    )

    assert capability.domains == ("market",)
    assert capability.intents == ("quote",)
    assert capability.safe_for_auto_execution is True
    assert capability.freshness_sensitive is True
    assert capability.direct_answer is True
    assert capability.output_contract == "narrative_context"


def test_fresh_structured_evidence_hard_gate_surfaces_are_removed():
    assert not hasattr(
        CapabilityMetadata,
        "provides_fresh_structured_evidence",
    )
    assert not hasattr(
        capability_module,
        "has_usable_structured_evidence",
    )
