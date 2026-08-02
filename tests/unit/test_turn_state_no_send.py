"""No-send evaluator safety boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from simpleclaw.agent.resolution_types import ExecutionMode
from simpleclaw.agent.turn_plan import _execution_mode

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/eval_turn_state_no_send.py"
SPEC = importlib.util.spec_from_file_location("turn_state_no_send", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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


@pytest.mark.parametrize(
    "mode",
    [
        ExecutionMode.ANSWER_WITH_EVIDENCE,
        ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
    ],
)
def test_fact_action_planned_accepts_canonical_fact_modes(
    mode: ExecutionMode,
) -> None:
    assert MODULE._fact_action_planned(
        fact_required=True,
        execution_mode=mode,
    )


@pytest.mark.parametrize(
    ("legacy_mode", "canonical_mode"),
    [
        ("fact_check", ExecutionMode.ANSWER_WITH_EVIDENCE),
        ("complex_fact", ExecutionMode.RESOLVE_COMPLEX_PROBLEM),
    ],
)
def test_fact_action_planned_preserves_legacy_mode_normalization(
    legacy_mode: str,
    canonical_mode: ExecutionMode,
) -> None:
    normalized_mode = _execution_mode(legacy_mode)

    assert normalized_mode is canonical_mode
    assert MODULE._fact_action_planned(
        fact_required=True,
        execution_mode=normalized_mode,
    )


def test_fact_action_planned_rejects_non_fact_direct_answer() -> None:
    assert not MODULE._fact_action_planned(
        fact_required=False,
        execution_mode=ExecutionMode.DIRECT_ANSWER,
    )


@pytest.mark.asyncio
async def test_canonical_fact_probe_passes_without_no_send_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        '[{"id":"lpga-current-result","user":"current result",'
        '"critical":true,"expected_mode":"answer_with_evidence",'
        '"expected_domain":"sports",'
        '"expected_intents":["current_result"]}]',
        encoding="utf-8",
    )
    plan = SimpleNamespace(
        execution=SimpleNamespace(mode=ExecutionMode.ANSWER_WITH_EVIDENCE),
        fact_check=SimpleNamespace(
            required=True,
            domain="sports",
            intents=("current_result",),
        ),
    )

    async def fake_plan_turn_with_llm(*_args: object, **_kwargs: object) -> object:
        return plan

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
    monkeypatch.setattr(MODULE, "build_planner_catalog", lambda **_kwargs: object())
    monkeypatch.setattr(MODULE, "_planner_native_specs", lambda **_kwargs: ())
    monkeypatch.setattr(MODULE, "plan_turn_with_llm", fake_plan_turn_with_llm)
    monkeypatch.setattr(
        MODULE,
        "PlanGate",
        lambda: SimpleNamespace(
            evaluate=lambda *_args, **_kwargs: SimpleNamespace(
                status=SimpleNamespace(value="pass")
            )
        ),
    )
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

    assert report["fact_required_without_action"] == 0
    assert report["critical_schema_gate_success_rate"] == 1.0
    assert report["session_context_contamination"] == 0
    assert report["unverified_final_count"] == 0
    assert report["unsupported_factual_final_count"] == 0
    assert report["cases"][0]["fact_action_planned"] is True
    assert report["no_dispatch"] is True
    assert report["no_persist"] is True
    assert report["no_delivery"] is True


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
