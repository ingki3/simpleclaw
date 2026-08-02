from __future__ import annotations

from simpleclaw.agent.resolution_ledger import attempt_signature
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    CapabilityCoverage,
    ExecutionMode,
    ResolutionBudget,
    decide_complex_escalation,
)


def test_execution_mode_has_exactly_four_wire_values() -> None:
    assert {item.value for item in ExecutionMode} == {
        "clarify",
        "direct_answer",
        "answer_with_evidence",
        "resolve_complex_problem",
    }


def test_unknown_side_effect_is_never_retryable() -> None:
    result = AssetResult(
        asset_type="skill",
        asset_name="calendar",
        status=AssetExecutionStatus.UNKNOWN_EFFECT,
        side_effect=True,
        retryable=True,
    )
    assert result.retryable is False
    assert decide_complex_escalation(
        result=result,
        fallback_allows_complex=True,
        budget=ResolutionBudget(max_steps=3),
    ).escalate is False


def test_attempt_signature_is_canonical_and_hides_raw_question() -> None:
    first = attempt_signature(
        question="  민감한   질문 ",
        asset_type="skill",
        asset_name="lookup",
        parameters={"b": 2, "a": 1},
    )
    second = attempt_signature(
        question="민감한 질문",
        asset_type="skill",
        asset_name="lookup",
        parameters={"a": 1, "b": 2},
    )
    assert first == second
    assert "민감" not in first


def test_capability_coverage_wire_values_are_stable() -> None:
    assert CapabilityCoverage.FULL.value == "full_coverage"
