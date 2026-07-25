"""BIZ-493 — Unified TurnPlanner rollout config 계약."""

from __future__ import annotations

from simpleclaw.config import load_agent_config


def test_unified_turn_planner_defaults_to_off(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("agent:\n  history_limit: 5\n", encoding="utf-8")

    assert load_agent_config(config)["unified_turn_planner"] == {
        "mode": "off",
        "sample_rate": 0.0,
        "max_tokens": 2048,
        "structured_output": True,
        "reasoning": {
            "enabled": True,
            "effort": "medium",
            "budget_tokens": 512,
        },
        "context_candidate_limit": 8,
        "context_candidate_max_chars": 6000,
        "selected_context_max_turns": 3,
        "selected_context_max_chars": 2400,
        "repair_attempts": 1,
        "telemetry": {
            "enabled": True,
            "include_raw_text": False,
        },
    }


def test_unified_turn_planner_loads_shadow_contract(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    mode: SHADOW
    sample_rate: 0.25
    max_tokens: 1024
    structured_output: true
    reasoning:
      enabled: false
      effort: HIGH
      budget_tokens: 256
    context_candidate_limit: 6
    context_candidate_max_chars: 4000
    selected_context_max_turns: 2
    selected_context_max_chars: 1200
    repair_attempts: 0
    telemetry:
      enabled: false
      include_raw_text: false
""",
        encoding="utf-8",
    )

    result = load_agent_config(config)["unified_turn_planner"]

    assert result == {
        "mode": "shadow",
        "sample_rate": 0.25,
        "max_tokens": 1024,
        "structured_output": True,
        "reasoning": {
            "enabled": False,
            "effort": "high",
            "budget_tokens": 256,
        },
        "context_candidate_limit": 6,
        "context_candidate_max_chars": 4000,
        "selected_context_max_turns": 2,
        "selected_context_max_chars": 1200,
        "repair_attempts": 0,
        "telemetry": {
            "enabled": False,
            "include_raw_text": False,
        },
    }


def test_unified_turn_planner_invalid_values_fail_closed(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    mode: live
    sample_rate: .nan
    context_candidate_limit: 2
    context_candidate_max_chars: 100
    selected_context_max_turns: 9
    selected_context_max_chars: 999
    telemetry:
      include_raw_text: true
""",
        encoding="utf-8",
    )

    result = load_agent_config(config)["unified_turn_planner"]

    assert result["mode"] == "off"
    assert result["sample_rate"] == 0.0
    assert result["selected_context_max_turns"] == 2
    assert result["selected_context_max_chars"] == 100
    assert result["telemetry"]["include_raw_text"] is False
