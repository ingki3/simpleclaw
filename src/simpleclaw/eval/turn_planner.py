"""Capability-first resolution 결과의 deterministic fixed-gold 채점."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CapabilityFirstExpected:
    asset_name: str
    coverage: str
    mode: str
    goal_status: str
    transition_mode: str = ""


@dataclass(frozen=True)
class CapabilityFirstActual:
    asset_name: str
    coverage: str
    mode: str
    goal_status: str
    transition_mode: str = ""
    complex_escalated: bool = False
    complexity_signals: tuple[str, ...] = ()
    repeated_signature_count: int = 0
    unsupported_final_claim_count: int = 0
    unknown_effect_retry_count: int = 0
    stop_reason: str = ""


def score_capability_first(
    expected: CapabilityFirstExpected,
    actual: CapabilityFirstActual,
) -> dict[str, bool]:
    """Architecture acceptance 축을 원문 없이 boolean으로 채점한다."""
    return {
        "exact_asset": actual.asset_name == expected.asset_name,
        "coverage": actual.coverage == expected.coverage,
        "mode": actual.mode == expected.mode,
        "goal_status": actual.goal_status == expected.goal_status,
        "transition": actual.transition_mode == expected.transition_mode,
        "false_complex_escalation": (
            not actual.complex_escalated or bool(actual.complexity_signals)
        ),
        "repeated_signature": actual.repeated_signature_count == 0,
        "unsupported_final_claim": actual.unsupported_final_claim_count == 0,
        "unknown_effect_auto_retry": actual.unknown_effect_retry_count == 0,
    }


def evaluate_capability_fixture_file(
    path: str | Path,
    *,
    max_cases: int | None = None,
) -> dict[str, object]:
    """No-send capability-first corpus의 wire shape를 검증한다."""
    rows: list[dict[str, object]] = []
    allowed_modes = {
        "clarify",
        "direct_answer",
        "answer_with_evidence",
        "resolve_complex_problem",
    }
    allowed_coverage = {
        "full_coverage",
        "partial_coverage",
        "no_match",
        "ambiguous",
        "needs_input",
        "needs_confirmation",
    }
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise TypeError(f"capability fixture line {line_number} must be object")
        required = {"id", "asset_name", "coverage", "mode", "goal_status"}
        if not required <= set(raw):
            raise ValueError(f"capability fixture line {line_number} missing fields")
        if raw["mode"] not in allowed_modes:
            raise ValueError(f"capability fixture line {line_number} invalid mode")
        if raw["coverage"] not in allowed_coverage:
            raise ValueError(f"capability fixture line {line_number} invalid coverage")
        rows.append(
            {
                "case_id": str(raw["id"]),
                "schema_valid": True,
                "mode": str(raw["mode"]),
                "coverage": str(raw["coverage"]),
                "goal_status": str(raw["goal_status"]),
                "transition_mode": str(raw.get("transition_mode") or ""),
                "stop_reason": "fixture_no_send",
            }
        )
        if max_cases is not None and len(rows) >= max_cases:
            break
    if not rows:
        raise ValueError("capability fixture has no cases")
    return {
        "schema": "capability-first-resolution-eval.v1",
        "case_count": len(rows),
        "schema_valid_rate": 1.0,
        "external_calls": 0,
        "cases": rows,
    }
