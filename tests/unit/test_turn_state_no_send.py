"""No-send evaluator safety boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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
