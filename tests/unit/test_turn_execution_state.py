"""BIZ-523 — explicit turn phase and factual finalization invariants."""

from __future__ import annotations

from dataclasses import replace

import pytest

from simpleclaw.agent.evidence_policy import (
    EvidenceFreshness,
    EvidenceSourceType,
    EvidenceState,
    EvidenceStatus,
)
from simpleclaw.agent.turn_plan import (
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.agent.turn_state import (
    InvalidTurnTransition,
    TurnExecutionState,
    TurnPhase,
)


def _fact_plan() -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="어제 유해란 LPGA 1라운드 성적 확인해줘",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="어제 유해란 LPGA 1라운드 성적 확인해줘",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("sports",),
        intents=("current_result",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="sports",
            intents=("current_result",),
            entities=(),
            reference_date="2026-07-30",
            search_query="유해란 LPGA 1라운드 2026-07-30 결과",
            required_claims=("1라운드 스코어", "순위"),
            freshness_required=True,
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.FACT_CHECK,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=("web_search",),
            requires_confirmation=False,
            reason="current result",
        ),
        confidence=0.98,
        decision_summary="sports current result",
    )


def _state() -> TurnExecutionState:
    state = TurnExecutionState.create(
        session_key="telegram:A",
        original_text="어제 유해란 LPGA 1라운드 성적 확인해줘",
        turn_id="turn-1",
    )
    state.attach_plan(_fact_plan())
    state.transition(TurnPhase.PLAN_GATED)
    return state


def test_fact_turn_cannot_finalize_before_verified_evidence() -> None:
    state = _state()
    state.transition(TurnPhase.EXECUTING)
    with pytest.raises(InvalidTurnTransition):
        state.transition(TurnPhase.FINALIZING)


def test_failed_evidence_goes_to_limited_final_not_not_found() -> None:
    state = _state()
    state.transition(TurnPhase.EXECUTING)
    state.transition(TurnPhase.COLLECTING_EVIDENCE)
    state.record_evidence(
        EvidenceState(
            required=True,
            attempted=True,
            status=EvidenceStatus.FAILED,
            source_type=EvidenceSourceType.STRUCTURED_REALTIME,
            freshness=EvidenceFreshness.UNKNOWN,
            failure_reason="provider_timeout",
        )
    )
    assert state.evidence.status is EvidenceStatus.FAILED
    assert state.next_phase() is TurnPhase.LIMITED_FINAL


def test_found_must_be_explicitly_verified_before_factual_final() -> None:
    state = _state()
    state.transition(TurnPhase.EXECUTING)
    state.transition(TurnPhase.COLLECTING_EVIDENCE)
    found = EvidenceState(
        required=True,
        attempted=True,
        status=EvidenceStatus.FOUND,
        source_type=EvidenceSourceType.STRUCTURED_REALTIME,
        freshness=EvidenceFreshness.CURRENT_TURN,
        evidence_text='{"facts":[{"source_url":"https://example.test"}]}',
    )
    state.record_evidence(found)
    assert not state.can_finalize()

    state.record_evidence(replace(found, status=EvidenceStatus.VERIFIED))
    assert state.can_finalize()
