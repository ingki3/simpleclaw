"""Agent package — lazily re-export AgentOrchestrator for compatibility."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simpleclaw.agent.orchestrator import AgentOrchestrator

__all__ = ["AgentOrchestrator"]


def __getattr__(name: str) -> Any:
    """Resolve the public orchestrator without eagerly importing the package graph."""
    if name != "AgentOrchestrator":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from simpleclaw.agent.orchestrator import AgentOrchestrator

    globals()[name] = AgentOrchestrator
    return AgentOrchestrator
