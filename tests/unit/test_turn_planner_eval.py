"""BIZ-488 — Unified TurnPlanner fixed-gold evaluator 계약 테스트."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from simpleclaw.evaluation.turn_planner_eval import (
    FixtureFormatError,
    aggregate_results,
    evaluate_fixture_replays,
    load_fixtures,
    percentile,
    score_prediction,
)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _fixture_row() -> dict[str, object]:
    return {
        "id": "sk-followup",
        "critical": True,
        "history": [
            {
                "id": "m101",
                "role": "user",
                "content": "Sk와 엔비디아 간의 협업 발표가 있었는데 이 내용 정리해줘",
            },
            {
                "id": "m102",
                "role": "assistant",
                "content": "API_KEY=raw-secret 최근 발표는 없다고 보입니다.",
            },
        ],
        "current": "오늘 있었던 발표야. 체크해봐 raw-user-secret",
        "gold": {
            "context_relation": "same_thread",
            "selected_turn_ids": ["m101"],
            "clarification_required": False,
            "execution_mode": "fact_check",
            "acceptable_assets": ["realtime-lookup-skill", "news-search-skill"],
            "fact_required": True,
            "domains": ["news"],
            "entities": ["SK", "NVIDIA"],
            "entity_aliases": {
                "SK": ["에스케이"],
                "NVIDIA": ["엔비디아"],
            },
            "normalized_terms": [["SK", "에스케이"], ["NVIDIA", "엔비디아"]],
            "forbidden_query_terms": ["5000억", "2GW", "HBM4 독점", "국가 LLM"],
        },
        "prediction": {
            "context": {
                "relation": "same_thread",
                "selected_turn_ids": ["m101"],
                "standalone_question": "SK와 엔비디아의 오늘 협업 발표를 확인해 정리해줘",
            },
            "clarification": {"required": False},
            "domains": ["news"],
            "fact_check": {
                "required": True,
                "domain": "news",
                "entities": ["에스케이", "NVIDIA"],
                "search_query": "SK NVIDIA 오늘 협업 발표",
            },
            "execution": {
                "mode": "fact_check",
                "primary_asset": {
                    "asset_type": "skill",
                    "name": "realtime-lookup-skill",
                },
            },
        },
        "metrics": {
            "latency_ms": 2500,
            "input_tokens": 3000,
            "output_tokens": 280,
        },
    }


def test_load_fixtures_accepts_valid_jsonl(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "cases.jsonl", [_fixture_row()])

    fixtures = load_fixtures(path)

    assert len(fixtures) == 1
    assert fixtures[0].id == "sk-followup"
    assert fixtures[0].gold.selected_turn_ids == ("m101",)
    assert fixtures[0].gold.entity_aliases["NVIDIA"] == ("엔비디아",)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-json\n", "line 1"),
        ('{"id":"x","current":"x"}\n', "history"),
        (
            json.dumps(
                {
                    **_fixture_row(),
                    "id": "bad-selected",
                    "gold": {
                        **_fixture_row()["gold"],
                        "selected_turn_ids": ["missing-id"],
                    },
                }
            )
            + "\n",
            "missing-id",
        ),
    ],
)
def test_load_fixtures_rejects_invalid_jsonl(
    tmp_path: Path,
    raw: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(FixtureFormatError, match=message):
        load_fixtures(path)


def test_load_fixtures_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = _write_jsonl(tmp_path / "duplicate.jsonl", [_fixture_row(), _fixture_row()])

    with pytest.raises(FixtureFormatError, match="duplicate fixture id"):
        load_fixtures(path)


def test_score_prediction_is_alias_aware_and_tracks_selected_precision_recall(
    tmp_path: Path,
) -> None:
    fixture = load_fixtures(
        _write_jsonl(tmp_path / "cases.jsonl", [_fixture_row()])
    )[0]

    result = score_prediction(
        fixture,
        fixture.prediction,
        latency_ms=2500,
        input_tokens=3000,
        output_tokens=280,
    )

    assert result.schema_valid is True
    assert result.selected_turn_precision == 1.0
    assert result.selected_turn_recall == 1.0
    assert result.checks["entities"] is True
    assert result.checks["normalized_terms"] is True
    assert result.passed is True


def test_topic_shift_false_context_and_forbidden_query_are_penalized(
    tmp_path: Path,
) -> None:
    row = _fixture_row()
    row["id"] = "topic-shift"
    row["gold"] = {
        **row["gold"],
        "context_relation": "topic_shift",
        "selected_turn_ids": [],
    }
    fixture = load_fixtures(
        _write_jsonl(tmp_path / "cases.jsonl", [row])
    )[0]
    prediction = {
        **fixture.prediction,
        "context": {
            "relation": "topic_shift",
            "selected_turn_ids": ["m101"],
            "standalone_question": "서울 날씨와 NVIDIA 2GW 발표를 확인해줘",
        },
        "fact_check": {
            **fixture.prediction["fact_check"],
            "search_query": "NVIDIA 2GW 국가 LLM 발표",
        },
    }

    result = score_prediction(fixture, prediction)

    assert result.checks["context_selection"] is False
    assert result.checks["topic_shift_no_context"] is False
    assert result.checks["neutral_query"] is False
    assert result.selected_turn_precision == 0.0
    assert result.selected_turn_recall == 1.0
    assert result.passed is False


def test_schema_errors_are_sanitized() -> None:
    row = _fixture_row()
    fixture = load_fixtures_from_rows([row])[0]

    result = score_prediction(fixture, {"secret": "API_KEY=do-not-report"})

    serialized = json.dumps(result.to_report(), ensure_ascii=False)
    assert result.schema_valid is False
    assert "missing:context" in result.error_codes
    assert "do-not-report" not in serialized


def test_stage_d_malformed_prediction_fails_closed() -> None:
    fixture = load_fixtures_from_rows([_fixture_row()])[0]
    prediction = {
        "context": {
            "relation": "same_thread",
            "selected_turn_ids": ["m101"],
            "standalone_question": "SK와 NVIDIA 발표를 확인해줘",
        },
        "clarification": {"required": False},
        "domains": "news",
        "fact_check": {
            "required": True,
            "domain": "news",
            "entities": "SK,NVIDIA",
            "search_query": "SK NVIDIA 발표",
        },
        "execution": {
            "mode": "fact_check",
            "primary_asset": {
                "asset_type": "skill",
                "asset_name": "realtime-lookup-skill",
            },
        },
    }

    result = score_prediction(fixture, prediction)

    assert result.schema_valid is False
    assert result.passed is False
    assert result.error_codes == (
        "invalid:domains",
        "invalid:fact_check.entities",
        "invalid:execution.primary_asset",
    )


@pytest.mark.parametrize(
    ("path", "value", "error_code"),
    [
        (("context", "selected_turn_ids"), ["m999"], "invalid:context.selected_turn_ids"),
        (("domains",), ["news", 7], "invalid:domains"),
        (("fact_check", "domain"), None, "invalid:fact_check.domain"),
        (("fact_check", "entities"), {"SK": True}, "invalid:fact_check.entities"),
        (("fact_check", "search_query"), None, "invalid:fact_check.search_query"),
        (("execution", "primary_asset"), 7, "invalid:execution.primary_asset"),
        (
            ("execution", "primary_asset"),
            {"asset_type": "tool", "name": "search"},
            "invalid:execution.primary_asset",
        ),
        (
            ("execution", "primary_asset"),
            {"asset_type": "skill", "name": "search", "secret": "credential"},
            "invalid:execution.primary_asset",
        ),
    ],
)
def test_prediction_nested_shape_violations_fail_closed(
    path: tuple[str, ...],
    value: object,
    error_code: str,
) -> None:
    fixture = load_fixtures_from_rows([_fixture_row()])[0]
    prediction = copy.deepcopy(fixture.prediction)
    target = prediction
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    result = score_prediction(fixture, prediction)

    assert result.schema_valid is False
    assert result.passed is False
    assert error_code in result.error_codes


def test_non_fact_search_query_must_still_be_a_string() -> None:
    row = _fixture_row()
    row["gold"] = {
        **row["gold"],
        "fact_required": False,
        "execution_mode": "direct_answer",
        "acceptable_assets": [],
    }
    prediction = copy.deepcopy(row["prediction"])
    prediction["fact_check"] = {
        "required": False,
        "domain": "none",
        "entities": [],
        "search_query": None,
    }
    prediction["execution"] = {
        "mode": "direct_answer",
        "primary_asset": None,
    }
    row["prediction"] = prediction
    fixture = load_fixtures_from_rows([row])[0]

    result = score_prediction(fixture, prediction)

    assert result.schema_valid is False
    assert result.error_codes == ("invalid:fact_check.search_query",)


def test_missing_primary_asset_differs_from_explicit_null() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "unified_turn_planner_cases.jsonl"
    )
    fixture = next(
        fixture
        for fixture in load_fixtures(fixture_path)
        if fixture.id == "static-cap-theorem"
    )
    prediction = copy.deepcopy(fixture.prediction)

    explicit_null_result = score_prediction(fixture, prediction)
    missing_prediction = copy.deepcopy(prediction)
    del missing_prediction["execution"]["primary_asset"]
    missing_result = score_prediction(fixture, missing_prediction)

    assert explicit_null_result.schema_valid is True
    assert explicit_null_result.checks["asset"] is True
    assert explicit_null_result.passed is True
    assert explicit_null_result.error_codes == ()
    assert missing_result.schema_valid is False
    assert missing_result.passed is False
    assert missing_result.error_codes == ("invalid:execution.primary_asset",)


@pytest.mark.parametrize(
    "primary_asset",
    [
        None,
        "__none__",
        {"asset_type": "skill", "name": "realtime-lookup-skill"},
        {"asset_type": "recipe", "name": "ai-report"},
    ],
)
def test_valid_primary_asset_shapes_preserve_scoring(
    primary_asset: object,
) -> None:
    row = _fixture_row()
    prediction = copy.deepcopy(row["prediction"])
    prediction["execution"]["primary_asset"] = primary_asset
    if primary_asset in (None, "__none__"):
        row["gold"] = {**row["gold"], "acceptable_assets": []}
    elif primary_asset["asset_type"] == "recipe":
        row["gold"] = {**row["gold"], "acceptable_assets": ["ai-report"]}
    row["prediction"] = prediction
    fixture = load_fixtures_from_rows([row])[0]

    result = score_prediction(fixture, prediction)

    assert result.schema_valid is True
    assert result.checks["asset"] is True


def test_malformed_nested_payload_values_are_not_reported() -> None:
    fixture = load_fixtures_from_rows([_fixture_row()])[0]
    prediction = copy.deepcopy(fixture.prediction)
    prediction["domains"] = ["news", {"API_KEY": "raw-credential"}]
    prediction["execution"]["primary_asset"] = {
        "asset_type": "skill",
        "name": "realtime-lookup-skill",
        "credential": "raw-credential",
    }

    result = score_prediction(fixture, prediction)
    serialized = json.dumps(result.to_report(), ensure_ascii=False)

    assert result.schema_valid is False
    assert "raw-credential" not in serialized
    assert "API_KEY" not in serialized


def load_fixtures_from_rows(rows: list[dict[str, object]]):
    """테스트용 임시 파일 없이 loader 계약을 재사용한다."""
    from simpleclaw.evaluation.turn_planner_eval import parse_fixture

    return [
        parse_fixture(row, source="test", line_number=index + 1)
        for index, row in enumerate(rows)
    ]


def test_percentile_and_repeated_aggregation_are_deterministic(
    tmp_path: Path,
) -> None:
    row = _fixture_row()
    fixture = load_fixtures(
        _write_jsonl(tmp_path / "cases.jsonl", [row])
    )[0]
    results = [
        score_prediction(
            fixture,
            fixture.prediction,
            latency_ms=value,
            input_tokens=100 + index,
            output_tokens=20 + index,
        )
        for index, value in enumerate([100, 200, 300, 400])
    ]

    report = aggregate_results(
        results,
        variant="medium",
        repeat=4,
        baseline="unified",
    )

    assert percentile([100, 200, 300, 400], 0.5) == 250
    assert percentile([100, 200, 300, 400], 0.95) == pytest.approx(385)
    assert report["summary"]["latency_ms"]["p50"] == 250
    assert report["summary"]["latency_ms"]["p95"] == pytest.approx(385)
    assert report["summary"]["tokens"]["input_total"] == 406
    assert report["summary"]["context_reduction_rate"] > 0
    assert report == aggregate_results(
        results,
        variant="medium",
        repeat=4,
        baseline="unified",
    )


def test_report_never_contains_raw_text_or_credentials(tmp_path: Path) -> None:
    fixture = load_fixtures(
        _write_jsonl(tmp_path / "cases.jsonl", [_fixture_row()])
    )[0]

    report = aggregate_results(
        [score_prediction(fixture, fixture.prediction)],
        variant="medium",
        repeat=1,
        baseline="unified",
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert "raw-user-secret" not in serialized
    assert "raw-secret" not in serialized
    assert "API_KEY" not in serialized
    assert "오늘 있었던 발표야" not in serialized
    assert "search_query" not in serialized


def test_repository_fixture_has_required_coverage() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "unified_turn_planner_cases.jsonl"
    )

    fixtures = load_fixtures(fixture_path)
    relation_counts: dict[str, int] = {}
    for fixture in fixtures:
        relation = fixture.gold.context_relation
        relation_counts[relation] = relation_counts.get(relation, 0) + 1

    assert len(fixtures) >= 30
    assert sum(fixture.critical and fixture.id.startswith("sk-nvidia") for fixture in fixtures) >= 2
    assert all(
        relation_counts.get(relation, 0) >= 3
        for relation in (
            "standalone",
            "same_thread",
            "related_reference",
            "topic_shift",
            "unclear",
        )
    )


def test_evaluate_fixture_replays_uses_same_report_schema_for_baselines(
    tmp_path: Path,
) -> None:
    fixture = load_fixtures(
        _write_jsonl(tmp_path / "cases.jsonl", [_fixture_row()])
    )

    unified = evaluate_fixture_replays(
        fixture,
        repeat=2,
        variant="medium",
        baseline="unified",
    )
    two_stage = evaluate_fixture_replays(
        fixture,
        repeat=2,
        variant="off",
        baseline="two_stage",
    )

    assert unified.keys() == two_stage.keys()
    assert unified["schema_version"] == "turn-planner-eval.v1"
    assert unified["benchmark"]["repeat"] == 2
    assert len(unified["cases"]) == 2


def test_cli_writes_deterministic_json_and_live_is_opt_in(tmp_path: Path) -> None:
    fixture_path = _write_jsonl(tmp_path / "cases.jsonl", [_fixture_row()])
    output_path = tmp_path / "report.json"
    script = Path(__file__).parents[2] / "scripts" / "eval_unified_turn_planner.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--fixture",
            str(fixture_path),
            "--repeat",
            "2",
            "--reasoning",
            "medium",
            "--json-output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        },
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["benchmark"] == {
        "baseline": "unified",
        "reasoning": "medium",
        "repeat": 2,
        "live": False,
    }
    assert "raw-user-secret" not in output_path.read_text(encoding="utf-8")

    live = subprocess.run(
        [
            sys.executable,
            str(script),
            "--fixture",
            str(fixture_path),
            "--live",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[2] / "src"),
        },
    )
    assert live.returncode != 0
    assert "live runner" in live.stderr.lower()
