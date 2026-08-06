"""BIZ-493 — Unified TurnPlanner rollout config 계약."""

from __future__ import annotations

from simpleclaw.config import load_agent_config


def test_unified_turn_planner_defaults_to_primary(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("agent:\n  history_limit: 5\n", encoding="utf-8")

    assert load_agent_config(config)["unified_turn_planner"] == {
        "mode": "primary",
        "architecture": "legacy_v2",
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
        "examples_prompt": "unified_turn_planner_examples",
        "planner_max_attempts": 2,
        "evidence_max_attempts": 2,
        "resolution_budget": {
            "max_steps": None,
            "max_tool_calls": None,
            "max_seconds": None,
            "max_tokens": None,
        },
        "resolution_budget_valid": False,
        "complex_escalation": {"enabled": False},
        "on_planner_failure": "fail_closed",
        "telemetry": {
            "enabled": True,
            "include_raw_text": False,
        },
    }


def test_unified_turn_planner_loads_shadow_contract_and_enforces_strict_policy(
    tmp_path,
):
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    mode: SHADOW
    sample_rate: 0.25
    max_tokens: 1024
    structured_output: false
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
        "architecture": "legacy_v2",
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
        "repair_attempts": 1,
        "examples_prompt": "unified_turn_planner_examples",
        "planner_max_attempts": 2,
        "evidence_max_attempts": 2,
        "resolution_budget": {
            "max_steps": None,
            "max_tool_calls": None,
            "max_seconds": None,
            "max_tokens": None,
        },
        "resolution_budget_valid": False,
        "complex_escalation": {"enabled": False},
        "on_planner_failure": "fail_closed",
        "telemetry": {
            "enabled": False,
            "include_raw_text": False,
        },
    }


def test_capability_first_budget_is_nullable_but_primary_requires_finite_axis(
    tmp_path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    architecture: capability_first_v3
    resolution_budget:
      max_steps: 4
      max_tool_calls: null
      max_seconds: 20
      max_tokens: null
    complex_escalation:
      enabled: true
""",
        encoding="utf-8",
    )
    result = load_agent_config(config)["unified_turn_planner"]
    assert result["architecture"] == "capability_first_v3"
    assert result["resolution_budget_valid"] is True
    assert result["resolution_budget"] == {
        "max_steps": 4,
        "max_tool_calls": None,
        "max_seconds": 20,
        "max_tokens": None,
    }
    assert result["complex_escalation"]["enabled"] is True


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


def test_unified_turn_planner_normalizes_canary_to_read_only_mode(tmp_path):
    """legacy canary 표기도 명시적 read-only rollout mode로 정규화한다."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    mode: CANARY
    sample_rate: 0.05
""",
        encoding="utf-8",
    )

    result = load_agent_config(config)["unified_turn_planner"]

    assert result["mode"] == "read_only_canary"
    assert result["sample_rate"] == 0.05


def test_langgraph_v4_requires_all_finite_budget_axes_and_keeps_architecture(
    tmp_path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    architecture: langgraph_v4
    mode: shadow
    langgraph_v4:
      budget:
        max_graph_steps: 40
        max_asset_calls: 12
        max_llm_calls: 8
        max_tokens: 16000
        max_seconds: 180
        max_parallel_invocations: 3
      checkpoint:
        path: ~/.simpleclaw-agent/default/test-graph.sqlite3
        terminal_ttl_days: 7
        max_rows: 2000
      delivery:
        mode: live
        max_attempts: 2
      shadow_no_send: false
""",
        encoding="utf-8",
    )

    result = load_agent_config(config)["unified_turn_planner"]

    assert result["architecture"] == "langgraph_v4"
    assert result["langgraph_v4"] == {
        "budget": {
            "max_graph_steps": 40,
            "max_asset_calls": 12,
            "max_llm_calls": 8,
            "max_tokens": 16000,
            "max_seconds": 180,
            "max_parallel_invocations": 3,
        },
        "budget_valid": True,
        "checkpoint": {
            "path": "~/.simpleclaw-agent/default/test-graph.sqlite3",
            "terminal_ttl_days": 7,
            "max_rows": 2000,
        },
        "delivery": {"mode": "no_send", "max_attempts": 2},
        "on_failure": "fail_closed",
        "shadow_no_send": True,
        "telemetry_fields": (
            "run_id",
            "request_id",
            "checkpoint_thread_id",
            "plan_id",
            "plan_revision",
            "catalog_fingerprint",
            "invocation_id",
            "definition_fingerprint",
            "contract_owner_ref",
            "input_contract_ref",
            "input_schema_hash",
            "payload_hash",
            "binding_ref",
            "output_contract_ref",
            "output_schema_hash",
            "selected_route",
            "invocation_status",
            "asset_result_status",
            "effect_status",
            "terminal_outcome",
            "delivery_status",
            "budget_usage",
            "model_call_attribution",
            "dispatch_trace",
            "stop_condition",
            "result_source",
            "provenance",
            "typed_final",
            "rollback_required",
            "rollback_reason",
        ),
    }


def test_langgraph_v4_invalid_budget_does_not_silently_fallback_to_legacy(
    tmp_path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """\
agent:
  unified_turn_planner:
    architecture: langgraph_v4
    langgraph_v4:
      budget:
        max_graph_steps: 20
        max_asset_calls: 0
        max_llm_calls: 4
        max_tokens: 8000
        max_seconds: .inf
""",
        encoding="utf-8",
    )

    result = load_agent_config(config)["unified_turn_planner"]

    assert result["architecture"] == "langgraph_v4"
    assert result["langgraph_v4"]["budget_valid"] is False
    assert result["langgraph_v4"]["budget"]["max_asset_calls"] is None
    assert result["langgraph_v4"]["budget"]["max_seconds"] is None
    assert result["langgraph_v4"]["budget"]["max_parallel_invocations"] is None
