from simpleclaw.eval.turn_planner import (
    CapabilityFirstActual,
    CapabilityFirstExpected,
    score_capability_first,
)


def test_capability_first_metrics_reject_false_complex_and_repeat() -> None:
    checks = score_capability_first(
        CapabilityFirstExpected(
            asset_name="naver-sports-skill",
            coverage="full_coverage",
            mode="answer_with_evidence",
            goal_status="resolved",
        ),
        CapabilityFirstActual(
            asset_name="naver-sports-skill",
            coverage="full_coverage",
            mode="answer_with_evidence",
            goal_status="resolved",
            complex_escalated=True,
            repeated_signature_count=1,
        ),
    )
    assert checks["false_complex_escalation"] is False
    assert checks["repeated_signature"] is False

