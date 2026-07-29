"""BIZ-512 계층별 metric·hard failure·privacy report."""

from __future__ import annotations

from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CandidateAsset,
    CompactIntentCall,
)
from simpleclaw.evaluation.functiongemma_dataset import SanitizedCase
from simpleclaw.evaluation.functiongemma_eval import (
    InferenceResult,
    comparison_report,
    evaluate_predictions,
)
from simpleclaw.evaluation.functiongemma_labeling import LabeledCase


def _case(number: int, *, fallback: bool = False) -> LabeledCase:
    candidate = CandidateAsset("skill:search", "skill", "search")
    label = CompactIntentCall(
        "standalone",
        "execute_asset" if not fallback else "tool_loop",
        ("news",),
        ("lookup",),
        "skill:search" if not fallback else NO_ASSET,
        fallback,
    )
    return LabeledCase(
        SanitizedCase(
            f"case:{number}", f"group:{number}", (), f"RAW-{number}",
            "telegram", f"fp:{number}", "test",
        ),
        (candidate, CandidateAsset(NO_ASSET, "none", NO_ASSET)),
        label,
        "catalog",
        0.9,
    )


def test_metrics_hard_failure_and_percentiles_are_separate() -> None:
    cases = [_case(1), _case(2, fallback=True)]
    valid = cases[0].label.to_native_call()
    unknown = {
        **cases[1].label.to_native_call(),
        "arguments": {
            **cases[1].label.to_arguments(),
            "primary_asset": "skill:unknown",
        },
    }
    report = evaluate_predictions(
        cases,
        [
            InferenceResult("case:1", valid, 10),
            InferenceResult("case:2", unknown, 30),
        ],
    )
    assert report["schema_valid_rate"] == 1.0
    assert report["boundary_acceptance_rate"] == 0.5
    assert report["hard_failures"] == {"boundary.unknown_asset": 1}
    assert report["hard_failure_summary"]["unknown_asset"] == 1
    assert report["hard_failure_summary"]["missing_fallback"] == 0
    assert report["latency_ms"] == {"p50": 20.0, "p95": 29.0}
    assert "RAW-" not in str(report)


def test_comparison_requires_hard_gate_and_ten_point_gain() -> None:
    base = evaluate_predictions([_case(1)], [InferenceResult("case:1", None, 1, False)])
    tuned = evaluate_predictions(
        [_case(1)],
        [InferenceResult("case:1", _case(1).label.to_native_call(), 1)],
    )
    comparison = comparison_report(base, tuned)
    assert comparison["hard_gate_passed"] is True
    assert comparison["recommend_shadow_integration"] is True
    assert base["domains_macro"]["f1"] == 0.0


def test_input_order_must_match() -> None:
    try:
        evaluate_predictions([_case(1)], [InferenceResult("other", {}, 1)])
    except ValueError as exc:
        assert "order" in str(exc)
    else:
        raise AssertionError("order mismatch must fail")
