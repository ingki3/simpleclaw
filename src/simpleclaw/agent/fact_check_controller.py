"""Typed fact-check controller enforcing retrieval before composition."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import TypeAlias

from simpleclaw.agent.action_result import ActionError, ActionResult
from simpleclaw.agent.evidence_policy import (
    EvidenceState,
    EvidenceStatus,
    assess_realtime_result,
    limited_fallback,
    requirement_from_turn_plan,
)
from simpleclaw.agent.turn_state import TurnExecutionState, TurnPhase
from simpleclaw.skills.realtime_contracts import (
    LookupStatus,
    RealtimeLookupRequest,
    RealtimeLookupResult,
)

LookupCallable: TypeAlias = Callable[
    [RealtimeLookupRequest],
    Awaitable[RealtimeLookupResult],
]
ComposeCallable: TypeAlias = Callable[
    [TurnExecutionState],
    Awaitable[str],
]


def _default_as_of() -> str:
    return datetime.now().astimezone().isoformat()


class FactCheckController:
    """Run bounded typed adapters and gate factual finalization."""

    def __init__(
        self,
        *,
        lookup: LookupCallable | Sequence[LookupCallable],
        compose: ComposeCallable,
        max_attempts: int = 2,
        as_of: Callable[[], str] = _default_as_of,
    ) -> None:
        lookups = (
            tuple(lookup)
            if isinstance(lookup, Sequence)
            else (lookup,)
        )
        if not lookups:
            raise ValueError("FactCheckController requires at least one lookup")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._lookups = lookups
        self._compose = compose
        self._max_attempts = max_attempts
        self._as_of = as_of

    async def run(self, state: TurnExecutionState) -> TurnExecutionState:
        if state.plan is None:
            raise ValueError("fact controller requires an attached plan")
        state.transition(TurnPhase.EXECUTING)
        state.transition(TurnPhase.COLLECTING_EVIDENCE)
        request = RealtimeLookupRequest.from_plan(
            state.plan,
            as_of_kst=self._as_of(),
        )
        requirement = requirement_from_turn_plan(state.plan)

        final_evidence = requirement.initial_state()
        attempts = min(self._max_attempts, len(self._lookups))
        for attempt_index, lookup in enumerate(self._lookups[:attempts], start=1):
            try:
                result = await lookup(request)
            except Exception as exc:  # noqa: BLE001
                result = RealtimeLookupResult(
                    request=request,
                    status=LookupStatus.FAILED,
                    evidence=(),
                    facts=(),
                    limitations=(f"lookup failed: {type(exc).__name__}",),
                    payload={
                        "lookup_status": "failed",
                        "confidence": "low",
                        "facts": [],
                        "limitations": [
                            f"lookup failed: {type(exc).__name__}"
                        ],
                    },
                )
            final_evidence = self._assess(requirement, result)
            state.record_evidence(final_evidence)
            state.add_action(
                self._action_record(
                    result=result,
                    attempt_index=attempt_index,
                )
            )
            if final_evidence.usable:
                state.verify_evidence()
                state.transition(TurnPhase.EVIDENCE_VERIFIED)
                state.transition(TurnPhase.FINALIZING)
                composed = self._compose(state)
                state.set_final_text(
                    await composed if inspect.isawaitable(composed) else str(composed)
                )
                state.transition(TurnPhase.COMPLETED)
                return state
            if final_evidence.status is EvidenceStatus.NOT_FOUND:
                break

        state.transition(TurnPhase.LIMITED_FINAL)
        state.set_final_text(limited_fallback(final_evidence), limited=True)
        state.transition(TurnPhase.COMPLETED)
        return state

    @staticmethod
    def _assess(requirement, result: RealtimeLookupResult) -> EvidenceState:
        payload = result.to_payload()
        return assess_realtime_result(
            requirement,
            payload,
            usable=result.status is LookupStatus.FOUND,
            as_of=result.request.as_of_kst,
            failure_reason=(
                result.limitations[0]
                if result.status
                in {
                    LookupStatus.FAILED,
                    LookupStatus.UNSUPPORTED,
                    LookupStatus.UNUSABLE,
                }
                and result.limitations
                else ""
            ),
        )

    @staticmethod
    def _action_record(
        *,
        result: RealtimeLookupResult,
        attempt_index: int,
    ) -> ActionResult:
        if result.status is LookupStatus.FOUND:
            action_status = "success"
        elif result.status is LookupStatus.NOT_FOUND:
            action_status = "not_found"
        else:
            action_status = "failure"
        return ActionResult(
            step_id=f"realtime_lookup_{attempt_index}",
            tool_name="realtime_lookup",
            tool_call_id=f"typed-realtime-{attempt_index}",
            action="collect_current_fact",
            status=action_status,
            data={
                "domain": result.request.domain,
                "intents": list(result.request.intents),
                "lookup_status": result.status.value,
            },
            error=(
                ActionError(
                    code=result.status.value,
                    message=result.limitations[0] if result.limitations else "",
                )
                if action_status == "failure"
                else None
            ),
        )
