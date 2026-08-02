"""PlanGate를 통과한 UnifiedTurnPlan의 상위 실행 mode dispatcher.

이 모듈은 ``execution.mode``를 callback 하나에 대응시키는 일만 담당한다.
planner, gate, asset 선택, tool loop 구현은 각각 호출자와 callback의 책임이다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from simpleclaw.agent.turn_plan import ExecutionMode
from simpleclaw.agent.turn_state import TurnExecutionState

ExecutionCallback = Callable[
    [TurnExecutionState],
    Awaitable[TurnExecutionState],
]


@dataclass(frozen=True)
class ExecutionCallbacks:
    """ExecutionMode별 실행 controller callback 묶음."""

    direct_answer: ExecutionCallback
    execute_asset: ExecutionCallback
    tool_loop: ExecutionCallback
    fact_check: ExecutionCallback
    complex_fact: ExecutionCallback
    recipe: ExecutionCallback
    clarify: ExecutionCallback


class ExecutionRouter:
    """검증된 plan을 mode에 대응하는 callback 하나로 전달한다."""

    def __init__(self, callbacks: ExecutionCallbacks) -> None:
        self._callbacks = callbacks

    async def dispatch(
        self,
        state: TurnExecutionState,
    ) -> TurnExecutionState:
        """Dispatch the same turn object selected by its immutable plan."""
        if state.plan is None:
            raise ValueError("execution router requires an attached plan")
        handlers = {
            ExecutionMode.CLARIFY: self._callbacks.clarify,
            ExecutionMode.DIRECT_ANSWER: self._callbacks.direct_answer,
            ExecutionMode.EXECUTE_ASSET: self._callbacks.execute_asset,
            ExecutionMode.TOOL_LOOP: self._callbacks.tool_loop,
            ExecutionMode.FACT_CHECK: self._callbacks.fact_check,
            ExecutionMode.COMPLEX_FACT: self._callbacks.complex_fact,
            ExecutionMode.RECIPE: self._callbacks.recipe,
        }
        return await handlers[state.plan.execution.mode](state)
