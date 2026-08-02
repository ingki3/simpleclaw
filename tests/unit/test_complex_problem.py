import asyncio
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.complex_problem import (
    ComplexProblemController,
    ComplexProblemState,
    ProblemNode,
)
from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    ResolutionBudget,
)
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


@pytest.mark.asyncio
async def test_complex_deadline_cancels_in_flight_node() -> None:
    async def slow_execute(*_args: object) -> AssetResult:
        await asyncio.sleep(0.05)
        return AssetResult(
            asset_type="skill",
            asset_name="x",
            status=AssetExecutionStatus.COMPLETED,
            resolved_claims=("a",),
        )

    state = ComplexProblemState(
        original_goal="resolve",
        nodes=[
            ProblemNode("a", "a", "a?", (), (AssetRef("skill", "x"),)),
        ],
        ledger=ResolutionLedger(),
    )
    outcome = await ComplexProblemController(execute_node=slow_execute).run(
        state,
        budget=ResolutionBudget.from_seconds(max_seconds=0.01, max_steps=2),
    )

    assert outcome.stop_reason == "terminal"
    assert outcome.limitations == ("deadline_exhausted",)
