"""No-send evaluator safety boundary."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.turn_plan import UnifiedTurnPlan, parse_turn_plan_data

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/eval_turn_state_no_send.py"
SPEC = importlib.util.spec_from_file_location("turn_state_no_send", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _plan(mode: str) -> UnifiedTurnPlan:
    return parse_turn_plan_data(
        {
            "context": {
                "relation": "standalone",
                "standalone_question": "오늘 결과를 확인해줘",
            },
            "clarification": {"required": False},
            "domains": ["sports"],
            "intents": ["current_result"],
            "fact_check": {
                "required": mode != "direct_answer",
                "owner": "planner" if mode != "direct_answer" else "none",
                "domain": "sports" if mode != "direct_answer" else "none",
                "intents": ["current_result"] if mode != "direct_answer" else [],
                "reference_date": "2026-08-02",
                "search_query": "오늘 경기 결과",
                "required_claims": ["오늘 경기 결과"],
                "freshness_required": mode != "direct_answer",
            },
            "execution": {"mode": mode},
        },
        original_text="오늘 결과를 확인해줘",
    )


def _args(fixture: Path) -> argparse.Namespace:
    return MODULE.build_parser().parse_args(
        [
            "--fixture",
            str(fixture),
            "--max-cases",
            "1",
            "--repeats",
            "1",
            "--no-dispatch",
            "--no-persist",
            "--no-delivery",
        ]
    )


def _mock_planner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan: UnifiedTurnPlan,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "load_agent_config",
        lambda _path: {
            "unified_turn_planner": {
                "max_tokens": 100,
                "reasoning": None,
                "examples_prompt": "unified_turn_planner_examples",
            }
        },
    )
    monkeypatch.setattr(MODULE, "create_router", lambda _path: object())
    monkeypatch.setattr(
        MODULE,
        "plan_turn_with_llm",
        AsyncMock(return_value=plan),
    )


def test_no_send_requires_all_three_safety_flags() -> None:
    with pytest.raises(SystemExit):
        MODULE.main(["--max-cases", "1", "--repeats", "1"])


def test_no_send_caps_provider_budget() -> None:
    with pytest.raises(SystemExit):
        MODULE.main(
            [
                "--max-cases",
                "13",
                "--repeats",
                "3",
                "--no-dispatch",
                "--no-persist",
                "--no-delivery",
            ]
        )


def test_fixture_loader_returns_only_attributed_user_cases(tmp_path: Path) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        '[{"id":"case-a","user":"secret text"},{"id":"skip"}]',
        encoding="utf-8",
    )
    assert [row["id"] for row in MODULE._load_cases(fixture, 12)] == ["case-a"]


def test_canonical_answer_with_evidence_is_fact_action_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        '[{"id":"canonical-fact","user":"today result"}]',
        encoding="utf-8",
    )
    _mock_planner(monkeypatch, plan=_plan("answer_with_evidence"))

    assert MODULE.main(
        [
            "--fixture",
            str(fixture),
            "--max-cases",
            "1",
            "--repeats",
            "1",
            "--no-dispatch",
            "--no-persist",
            "--no-delivery",
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["fact_required_without_action"] == 0
    assert report["cases"][0]["fact_action_planned"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("legacy_mode", "canonical_mode"),
    [
        ("fact_check", "answer_with_evidence"),
        ("complex_fact", "resolve_complex_problem"),
    ],
)
async def test_legacy_fact_modes_are_normalized_before_evaluator_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    legacy_mode: str,
    canonical_mode: str,
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        '[{"id":"legacy-fact","user":"today result"}]',
        encoding="utf-8",
    )
    plan = _plan(legacy_mode)
    _mock_planner(monkeypatch, plan=plan)

    report = await MODULE._run(_args(fixture))

    assert plan.execution.mode.value == canonical_mode
    assert report["fact_required_without_action"] == 0
    assert report["cases"][0]["fact_action_planned"] is True


@pytest.mark.asyncio
async def test_direct_answer_is_not_fact_action_and_no_send_remains_clean(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        '[{"id":"direct","user":"explain this"}]',
        encoding="utf-8",
    )
    _mock_planner(monkeypatch, plan=_plan("direct_answer"))

    report = await MODULE._run(_args(fixture))

    assert report["cases"][0]["fact_required"] is False
    assert report["cases"][0]["fact_action_planned"] is False
    assert report["fact_required_without_action"] == 0
    assert report["no_dispatch"] is True
    assert report["no_persist"] is True
    assert report["no_delivery"] is True
    assert report["session_context_contamination"] == 0
    assert report["unverified_final_count"] == 0
    assert report["unsupported_factual_final_count"] == 0


@pytest.mark.asyncio
async def test_injected_planner_failure_is_fail_closed_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        '[{"id":"planner-down","user":"current result",'
        '"planner_error":true}]',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        MODULE,
        "load_agent_config",
        lambda _path: {
            "unified_turn_planner": {
                "max_tokens": 100,
                "reasoning": None,
                "examples_prompt": "unified_turn_planner_examples",
            }
        },
    )
    monkeypatch.setattr(MODULE, "create_router", lambda _path: object())
    args = MODULE.build_parser().parse_args(
        [
            "--fixture",
            str(fixture),
            "--max-cases",
            "1",
            "--repeats",
            "1",
            "--no-dispatch",
            "--no-persist",
            "--no-delivery",
        ]
    )

    report = await MODULE._run(args)

    assert report["keyword_fallback_count"] == 0
    assert report["fact_required_without_action"] == 0
    assert report["planner_call_count"] == 1
    assert report["cases"][0]["injected_planner_failure"] is True
