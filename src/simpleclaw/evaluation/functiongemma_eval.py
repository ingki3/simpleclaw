"""base/tuned FunctionGemma를 동일 입력에서 계층별로 비교한다."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CompactIntentCall,
    FunctionCallContractError,
    parse_function_call,
)
from simpleclaw.evaluation.functiongemma_labeling import LabeledCase


@dataclass(frozen=True)
class InferenceResult:
    case_id: str
    payload: object | None
    latency_ms: float
    api_success: bool = True
    error_code: str = ""


def canonical_json_sha256(value: object) -> str:
    """UTF-8 canonical JSON(sort keys, compact separators)의 SHA-256."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    """파일에 기록된 실제 byte sequence의 SHA-256."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _macro_prf(
    expected: Sequence[tuple[str, ...]],
    predicted: Sequence[tuple[str, ...]],
) -> dict[str, float]:
    labels = sorted({
        label for row in (*expected, *predicted) for label in row
    })
    if not expected:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if not labels:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    scores = []
    for label in labels:
        tp = sum(label in gold and label in pred for gold, pred in zip(expected, predicted))
        fp = sum(label not in gold and label in pred for gold, pred in zip(expected, predicted))
        fn = sum(label in gold and label not in pred for gold, pred in zip(expected, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        scores.append((precision, recall, f1))
    return {
        "precision": statistics.fmean(row[0] for row in scores),
        "recall": statistics.fmean(row[1] for row in scores),
        "f1": statistics.fmean(row[2] for row in scores),
    }


def evaluate_predictions(
    cases: Sequence[LabeledCase],
    results: Sequence[InferenceResult],
) -> dict[str, Any]:
    """원문/payload를 report에 싣지 않고 soft metric과 hard failure를 분리한다."""
    expected_by_id = {item.case.case_id: item for item in cases}
    if [item.case.case_id for item in cases] != [row.case_id for row in results]:
        raise ValueError("base/tuned evaluation input order mismatch")
    parsed: list[CompactIntentCall | None] = []
    hard = Counter()
    native_call_count = 0
    schema_valid_count = 0
    boundary_accepted_count = 0
    for result in results:
        case = expected_by_id[result.case_id]
        if not result.api_success or result.payload is None:
            hard["inference_failure"] += 1
            parsed.append(None)
            continue
        if (
            isinstance(result.payload, Mapping)
            and set(result.payload) == {"name", "arguments"}
        ) or (
            isinstance(result.payload, str)
            and "<start_function_call>" in result.payload
        ):
            native_call_count += 1
        try:
            parsed.append(parse_function_call(
                result.payload,
                candidate_ids=[
                    candidate.asset_id
                    for candidate in case.candidates
                    if candidate.asset_id != NO_ASSET
                ],
            ))
            schema_valid_count += 1
            boundary_accepted_count += 1
        except FunctionCallContractError as exc:
            hard[exc.code] += 1
            if exc.code.startswith("boundary."):
                schema_valid_count += 1
            parsed.append(None)

    total = len(cases)
    valid_pairs = [
        (case.label, prediction)
        for case, prediction in zip(cases, parsed)
        if prediction is not None
    ]
    valid_predictions = [prediction for _, prediction in valid_pairs]
    valid_expected = [expected for expected, _ in valid_pairs]
    exact_relation = sum(
        gold.context_relation == pred.context_relation
        for gold, pred in valid_pairs
    )
    exact_mode = sum(
        gold.execution_mode == pred.execution_mode for gold, pred in valid_pairs
    )
    exact_asset = sum(
        gold.primary_asset == pred.primary_asset for gold, pred in valid_pairs
    )
    fallback_expected = [
        index for index, (gold, _) in enumerate(valid_pairs)
        if gold.fallback_required
    ]
    fallback_recall = (
        sum(valid_predictions[index].fallback_required for index in fallback_expected)
        / len(fallback_expected)
        if fallback_expected else (1.0 if valid_pairs else 0.0)
    )
    denomin = len(valid_pairs) or 1
    hard_summary = {
        "inference": hard["inference_failure"],
        "native_or_schema": sum(
            count for code, count in hard.items() if code.startswith("schema.")
        ),
        "boundary": sum(
            count for code, count in hard.items() if code.startswith("boundary.")
        ),
        "unknown_asset": hard["boundary.unknown_asset"],
        "missing_fallback": hard["boundary.missing_fallback"],
    }
    return {
        "case_count": total,
        "api_success_rate": sum(row.api_success for row in results) / total if total else 0.0,
        "native_function_call_rate": native_call_count / total if total else 0.0,
        "schema_valid_rate": schema_valid_count / total if total else 0.0,
        "boundary_acceptance_rate": (
            boundary_accepted_count / total if total else 0.0
        ),
        "context_relation_accuracy": exact_relation / denomin,
        "execution_mode_accuracy": exact_mode / denomin,
        "domains_macro": _macro_prf(
            [item.domains for item in valid_expected],
            [item.domains for item in valid_predictions],
        ),
        "intents_macro": _macro_prf(
            [item.intents for item in valid_expected],
            [item.intents for item in valid_predictions],
        ),
        "primary_asset_accuracy": exact_asset / denomin,
        "fallback_recall": fallback_recall,
        "latency_ms": {
            "p50": _percentile([row.latency_ms for row in results], 0.5),
            "p95": _percentile([row.latency_ms for row in results], 0.95),
        },
        "hard_failures": dict(sorted(hard.items())),
        "hard_failure_summary": hard_summary,
    }


def compact_macro_score(report: Mapping[str, Any]) -> float:
    fields = (
        float(report["context_relation_accuracy"]),
        float(report["execution_mode_accuracy"]),
        float(report["domains_macro"]["f1"]),
        float(report["intents_macro"]["f1"]),
        float(report["primary_asset_accuracy"]),
        float(report["fallback_recall"]),
    )
    return statistics.fmean(fields)


def comparison_report(
    base: Mapping[str, Any],
    tuned: Mapping[str, Any],
) -> dict[str, Any]:
    base_score = compact_macro_score(base)
    tuned_score = compact_macro_score(tuned)
    hard_gate_passed = not tuned["hard_failures"]
    return {
        "metric_name": "Gemini/Unified emulation quality",
        "base": dict(base),
        "tuned": dict(tuned),
        "compact_macro_score": {
            "base": base_score,
            "tuned": tuned_score,
            "improvement_percentage_points": (tuned_score - base_score) * 100,
        },
        "hard_gate_passed": hard_gate_passed,
        "recommend_shadow_integration": (
            hard_gate_passed and tuned_score - base_score >= 0.10
        ),
    }
