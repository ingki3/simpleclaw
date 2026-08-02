"""하위 호환을 위해 ``AgentOrchestrator``를 지연 노출하는 Agent 패키지."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simpleclaw.agent.orchestrator import AgentOrchestrator

__all__ = ["AgentOrchestrator"]


def __getattr__(name: str) -> Any:
    """에이전트 그래프를 즉시 import하지 않고 공개 오케스트레이터를 불러온다."""
    if name != "AgentOrchestrator":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from simpleclaw.agent.orchestrator import AgentOrchestrator

    globals()[name] = AgentOrchestrator
    return AgentOrchestrator
