"""Single-turn execution state and explicit transition invariants."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from simpleclaw.agent.action_result import ActionResultLedger
from simpleclaw.agent.evidence_policy import EvidenceState, EvidenceStatus
from simpleclaw.agent.turn_plan import UnifiedTurnPlan

if TYPE_CHECKING:
    from simpleclaw.agent.plan_gate import PlanGateResult
    from simpleclaw.agent.tool_gate import ToolExecutionScope


class TurnPhase(str, Enum):
    """Lifecycle of exactly one ``process_message`` execution."""

    RECEIVED = "received"
    PLANNED = "planned"
    PLAN_GATED = "plan_gated"
    WAITING_FOR_USER = "waiting_for_user"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COLLECTING_EVIDENCE = "collecting_evidence"
    EVIDENCE_VERIFIED = "evidence_verified"
    LIMITED_FINAL = "limited_final"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidTurnTransition(RuntimeError):
    """Raised when a controller attempts an undeclared phase edge."""


_ALLOWED_TRANSITIONS: dict[TurnPhase, frozenset[TurnPhase]] = {
    TurnPhase.RECEIVED: frozenset({TurnPhase.PLANNED, TurnPhase.FAILED}),
    TurnPhase.PLANNED: frozenset(
        {
            TurnPhase.PLAN_GATED,
            TurnPhase.WAITING_FOR_USER,
            TurnPhase.REJECTED,
            TurnPhase.FAILED,
        }
    ),
    TurnPhase.PLAN_GATED: frozenset(
        {
            TurnPhase.WAITING_FOR_USER,
            TurnPhase.REJECTED,
            TurnPhase.EXECUTING,
            TurnPhase.FAILED,
        }
    ),
    TurnPhase.EXECUTING: frozenset(
        {
            TurnPhase.COLLECTING_EVIDENCE,
            TurnPhase.FINALIZING,
            TurnPhase.LIMITED_FINAL,
            TurnPhase.FAILED,
        }
    ),
    TurnPhase.COLLECTING_EVIDENCE: frozenset(
        {
            TurnPhase.EVIDENCE_VERIFIED,
            TurnPhase.LIMITED_FINAL,
            TurnPhase.FAILED,
        }
    ),
    TurnPhase.EVIDENCE_VERIFIED: frozenset(
        {TurnPhase.FINALIZING, TurnPhase.FAILED}
    ),
    TurnPhase.LIMITED_FINAL: frozenset(
        {TurnPhase.COMPLETED, TurnPhase.FAILED}
    ),
    TurnPhase.FINALIZING: frozenset(
        {TurnPhase.COMPLETED, TurnPhase.FAILED}
    ),
    TurnPhase.WAITING_FOR_USER: frozenset(),
    TurnPhase.REJECTED: frozenset(),
    TurnPhase.COMPLETED: frozenset(),
    TurnPhase.FAILED: frozenset(),
}


@dataclass
class TurnExecutionState:
    """Authoritative mutable execution state for one immutable plan."""

    turn_id: str
    session_key: str
    original_text: str
    plan: UnifiedTurnPlan | None = None
    phase: TurnPhase = TurnPhase.RECEIVED
    gate_result: PlanGateResult | None = None
    execution_scope: ToolExecutionScope | None = None
    evidence: EvidenceState = field(
        default_factory=lambda: EvidenceState(
            required=False,
            attempted=False,
            status=EvidenceStatus.NOT_SEARCHED,
        )
    )
    action_ledger: ActionResultLedger = field(default_factory=ActionResultLedger)
    limitations: list[str] = field(default_factory=list)
    final_text: str | None = None

    @classmethod
    def create(
        cls,
        *,
        session_key: str,
        original_text: str,
        turn_id: str | None = None,
    ) -> TurnExecutionState:
        return cls(
            turn_id=turn_id or uuid4().hex,
            session_key=session_key,
            original_text=original_text,
        )

    def attach_plan(self, plan: UnifiedTurnPlan) -> None:
        if self.phase is not TurnPhase.RECEIVED or self.plan is not None:
            raise InvalidTurnTransition("plan can be attached exactly once")
        if plan.original_text != self.original_text:
            raise ValueError("plan original_text must match turn original_text")
        self.plan = plan
        self.phase = TurnPhase.PLANNED

    def attach_gate_result(
        self,
        gate_result: PlanGateResult,
        *,
        execution_scope: ToolExecutionScope | None = None,
    ) -> None:
        if self.phase is not TurnPhase.PLANNED:
            raise InvalidTurnTransition("gate result requires planned phase")
        self.gate_result = gate_result
        if gate_result.effective_plan is not None:
            self.plan = gate_result.effective_plan
        self.execution_scope = execution_scope
        self.transition(TurnPhase.PLAN_GATED)

    def transition(self, target: TurnPhase) -> None:
        if target not in _ALLOWED_TRANSITIONS[self.phase]:
            raise InvalidTurnTransition(
                f"invalid turn transition: {self.phase.value} -> {target.value}"
            )
        if target is TurnPhase.FINALIZING and not self.can_finalize():
            raise InvalidTurnTransition(
                "factual turn cannot finalize without verified evidence"
            )
        self.phase = target

    def record_evidence(self, evidence: EvidenceState) -> None:
        self.evidence = evidence
        if evidence.failure_reason:
            self.limitations.append(evidence.failure_reason)

    def verify_evidence(self) -> None:
        if not self.evidence.usable:
            raise ValueError("only usable evidence can be verified")
        self.evidence = replace(
            self.evidence,
            status=EvidenceStatus.VERIFIED,
        )

    def can_finalize(self) -> bool:
        if self.plan is None:
            return False
        if not self.plan.fact_check.required:
            return True
        return self.evidence.status is EvidenceStatus.VERIFIED

    def next_phase(self) -> TurnPhase:
        if self.plan is None:
            return TurnPhase.FAILED
        if not self.plan.fact_check.required:
            return TurnPhase.FINALIZING
        if self.evidence.status is EvidenceStatus.VERIFIED:
            return TurnPhase.EVIDENCE_VERIFIED
        if self.evidence.status in {
            EvidenceStatus.NOT_FOUND,
            EvidenceStatus.FAILED,
            EvidenceStatus.UNUSABLE,
        }:
            return TurnPhase.LIMITED_FINAL
        return TurnPhase.COLLECTING_EVIDENCE

    def set_final_text(self, text: str, *, limited: bool = False) -> None:
        expected = TurnPhase.LIMITED_FINAL if limited else TurnPhase.FINALIZING
        if self.phase is not expected:
            raise InvalidTurnTransition(
                f"final text requires {expected.value} phase"
            )
        self.final_text = text

    def add_action(self, result: Any) -> None:
        self.action_ledger.append(result)
