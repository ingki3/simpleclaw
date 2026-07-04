import pytest

from simpleclaw.agent.dspy_fact_program import load_dspy_fact_program


def test_dspy_backend_reports_unavailable_without_dependency(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None if name == "dspy" else object())
    with pytest.raises(RuntimeError, match="DSPy backend is not installed"):
        load_dspy_fact_program()
