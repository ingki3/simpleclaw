from simpleclaw.config import load_agent_config


def test_complex_fact_workflow_defaults_disabled(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("agent: {}\n", encoding="utf-8")

    agent = load_agent_config(config)

    cfg = agent["complex_fact_workflow"]
    assert cfg["enabled"] is False
    assert cfg["route_threshold"] == 3
    assert cfg["max_iterations"] == 4
    assert cfg["max_sources_per_slot"] == 3
    assert cfg["planner_backend"] == "simpleclaw"
    assert cfg["enable_claim_verifier"] is True


def test_complex_fact_workflow_config_overrides(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
agent:
  complex_fact_workflow:
    enabled: true
    route_threshold: 4
    max_iterations: 2
    max_sources_per_slot: 1
    planner_backend: dspy
    enable_claim_verifier: false
""",
        encoding="utf-8",
    )

    cfg = load_agent_config(config)["complex_fact_workflow"]

    assert cfg["enabled"] is True
    assert cfg["route_threshold"] == 4
    assert cfg["max_iterations"] == 2
    assert cfg["max_sources_per_slot"] == 1
    assert cfg["planner_backend"] == "dspy"
    assert cfg["enable_claim_verifier"] is False
