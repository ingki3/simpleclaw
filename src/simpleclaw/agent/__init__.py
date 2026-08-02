"""Agent package with a lazy ``AgentOrchestrator`` compatibility export."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simpleclaw.agent.orchestrator import AgentOrchestrator

__all__ = ["AgentOrchestrator"]


def __getattr__(name: str) -> Any:
    """Load the public orchestrator without eagerly importing the agent graph."""
    if name != "AgentOrchestrator":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from simpleclaw.agent.orchestrator import AgentOrchestrator

    globals()[name] = AgentOrchestrator
    return AgentOrchestrator
