"""Task-local runtime budget hook shared by orchestration and LLM routing."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol


class RuntimeLLMBudget(Protocol):
    """LLM provider call 직전 reserve와 종료 후 reconciliation 계약."""

    def reserve_llm_call(self, max_tokens: int | None) -> tuple[int, object]: ...

    def complete_llm_call(
        self,
        ticket: object,
        usage: Mapping[str, object] | None,
    ) -> None: ...


_runtime_llm_budget_var: ContextVar[RuntimeLLMBudget | None] = ContextVar(
    "simpleclaw_runtime_llm_budget",
    default=None,
)


@dataclass(slots=True)
class RuntimeLLMReservation:
    """Router가 provider request cap과 completion을 한 번만 적용하게 한다."""

    max_tokens: int
    _budget: RuntimeLLMBudget
    _ticket: object
    _completed: bool = False

    def complete(self, usage: Mapping[str, object] | None) -> None:
        if self._completed:
            return
        self._completed = True
        self._budget.complete_llm_call(self._ticket, usage)


@contextmanager
def bind_runtime_llm_budget(budget: RuntimeLLMBudget):
    """현재 async task에서 파생된 provider calls에만 budget을 전파한다."""
    token = _runtime_llm_budget_var.set(budget)
    try:
        yield
    finally:
        _runtime_llm_budget_var.reset(token)


def reserve_runtime_llm_call(
    max_tokens: int | None,
) -> RuntimeLLMReservation | None:
    """활성 shadow budget이 있으면 provider 진입 전에 call/token을 예약한다."""
    budget = _runtime_llm_budget_var.get()
    if budget is None:
        return None
    capped_tokens, ticket = budget.reserve_llm_call(max_tokens)
    return RuntimeLLMReservation(capped_tokens, budget, ticket)
