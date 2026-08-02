from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.complex_problem import (
    ComplexProblemController,
    ComplexProblemState,
    ProblemNode,
)
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import ResolutionBudget
from simpleclaw.agent.turn_plan import AssetRef


@pytest.mark.asyncio
async def test_dependency_cycle_stops_without_execution() -> None:
    execute = AsyncMock()
    state = ComplexProblemState(
        original_goal="resolve",
        nodes=[
            ProblemNode("a", "a", "a?", ("b",), (AssetRef("skill", "x"),)),
            ProblemNode("b", "b", "b?", ("a",), (AssetRef("skill", "x"),)),
        ],
        ledger=ResolutionLedger(),
    )
    outcome = await ComplexProblemController(execute_node=execute).run(
        state,
        budget=ResolutionBudget(max_steps=3),
    )
    assert outcome.stop_reason == "dependency_cycle"
    execute.assert_not_awaited()

