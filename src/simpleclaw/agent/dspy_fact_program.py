"""Optional DSPy backend for complex fact planning.

Phase 1 does not require DSPy. This module exists so future implementation can
add DSPy Signatures/Modules without changing core workflow interfaces.
"""

from __future__ import annotations

import importlib.util


def load_dspy_fact_program():
    """Load the optional DSPy backend, or explain why it is unavailable."""

    if importlib.util.find_spec("dspy") is None:
        raise RuntimeError(
            "DSPy backend is not installed. Install optional DSPy dependencies and set "
            "agent.complex_fact_workflow.planner_backend=dspy only after validation."
        )
    raise NotImplementedError("DSPy complex fact backend is planned but not implemented yet")
