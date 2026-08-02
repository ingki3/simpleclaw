"""BIZ-523 — retrieval/evidence state gates factual composition."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.evidence_policy import EvidenceStatus
from simpleclaw.agent.fact_check_controller import FactCheckController
from simpleclaw.agent.turn_plan import (
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    FactEntity,
    UnifiedTurnPlan,
)
from simpleclaw.agent.turn_state import TurnExecutionState, TurnPhase
from simpleclaw.skills.realtime_contracts import (
    LookupStatus,
    RealtimeLookupRequest,
    RealtimeLookupResult,
)


def _plan(
    *,
    required_claims: tuple[str, ...] = ("점수", "승패"),
) -> UnifiedTurnPlan:
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
            entities=(
                FactEntity("athlete", "유해란"),
                FactEntity("league", "LPGA"),
                FactEntity("round", "1"),
            ),
            reference_date="2026-07-30",
            search_query="유해란 LPGA 1라운드 2026-07-30",
            required_claims=required_claims,
            freshness_required=True,
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.FACT_CHECK,
            primary_asset=None,
            allowed_assets=(),
            allowed_tools=("web_search",),
            requires_confirmation=False,
            reason="verify current result",
        ),
        confidence=0.99,
        decision_summary="sports result",
    )


def _state(
    *,
    required_claims: tuple[str, ...] = ("점수", "승패"),
) -> TurnExecutionState:
    plan = _plan(required_claims=required_claims)
    state = TurnExecutionState.create(
        session_key="telegram:A",
        original_text=plan.original_text,
        turn_id="turn-1",
    )
    state.attach_plan(plan)
    state.transition(TurnPhase.PLAN_GATED)
    return state


def _request(
    *,
    required_claims: tuple[str, ...] = ("점수", "승패"),
) -> RealtimeLookupRequest:
    return RealtimeLookupRequest.from_plan(
        _plan(required_claims=required_claims),
        as_of_kst="2026-07-31T08:32:15+09:00",
    )


def _result(
    status: LookupStatus,
    *,
    required_claims: tuple[str, ...] = ("점수", "승패"),
) -> RealtimeLookupResult:
    request = _request(required_claims=required_claims)
    if status is LookupStatus.FOUND:
        payload = {
            "lookup_status": "found",
            "kind": "sports",
            "confidence": "high",
            "facts": [
                {
                    "type": "sports_score",
                    "league": "LPGA",
                    "event_date": "2026-07-30",
                    "away_team": "유해란",
                    "away_score": 68,
                    "home_team": "field",
                    "home_score": 69,
                    "status": "final",
                    "winner": "유해란",
                    "source": "LPGA",
                    "source_url": "https://example.test/lpga",
                }
            ],
            "timeline_validation": {"status": "final"},
            "limitations": [],
        }
        facts = tuple(payload["facts"])
        evidence = tuple(payload["facts"])
        limitations = ()
    else:
        payload = {
            "lookup_status": status.value,
            "confidence": "low",
            "facts": [],
            "limitations": ["provider unavailable"],
        }
        facts = ()
        evidence = ()
        limitations = ("provider unavailable",)
    return RealtimeLookupResult(
        request=request,
        status=status,
        evidence=evidence,
        facts=facts,
        limitations=limitations,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_failed_lookup_returns_limited_final_without_composer() -> None:
    composer = AsyncMock()
    controller = FactCheckController(
        lookup=AsyncMock(return_value=_result(LookupStatus.FAILED)),
        compose=composer,
        max_attempts=1,
        as_of=lambda: "2026-07-31T08:32:15+09:00",
    )

    state = await controller.run(_state())

    composer.assert_not_awaited()
    assert state.phase is TurnPhase.COMPLETED
    assert state.evidence.status is EvidenceStatus.FAILED
    assert "조회가 실패" in (state.final_text or "")
    assert "대회가 없습니다" not in (state.final_text or "")
    assert len(state.action_ledger.results) == 1


@pytest.mark.asyncio
async def test_verified_evidence_is_required_before_composer() -> None:
    composer = AsyncMock(return_value="검증된 결과입니다.")
    lookup = AsyncMock(return_value=_result(LookupStatus.FOUND))
    controller = FactCheckController(
        lookup=lookup,
        compose=composer,
        max_attempts=1,
        as_of=lambda: "2026-07-31T08:32:15+09:00",
    )

    state = await controller.run(_state())

    request = lookup.await_args.args[0]
    assert request.domain == "sports"
    assert request.intents == ("current_result",)
    assert request.entity("league") == "LPGA"
    assert state.evidence.status is EvidenceStatus.VERIFIED
    assert state.phase is TurnPhase.COMPLETED
    assert state.final_text == "검증된 결과입니다."
    composer.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsupported_adapter_is_not_coerced_to_not_found() -> None:
    composer = AsyncMock()
    controller = FactCheckController(
        lookup=AsyncMock(return_value=_result(LookupStatus.UNSUPPORTED)),
        compose=composer,
        max_attempts=1,
    )
    state = await controller.run(_state())
    assert state.evidence.status is EvidenceStatus.UNUSABLE
    assert "확정할 수 없습니다" in (state.final_text or "")
    composer.assert_not_awaited()


@pytest.mark.parametrize(
    ("payload_update", "fact_update"),
    [
        ({"confidence": "low"}, {}),
        ({"timeline_validation": {"status": "stale_or_pre_event"}}, {}),
        ({}, {"event_date": "2026-07-29"}),
        (
            {"timeline_validation": {"status": "partial"}},
            {"status": "scheduled", "winner": None},
        ),
    ],
)
@pytest.mark.asyncio
async def test_found_semantically_unusable_payload_never_enters_composer(
    payload_update: dict,
    fact_update: dict,
) -> None:
    result = _result(LookupStatus.FOUND)
    payload = dict(result.payload)
    payload.update(payload_update)
    facts = [dict(result.facts[0])]
    facts[0].update(fact_update)
    payload["facts"] = facts
    unusable = RealtimeLookupResult(
        request=result.request,
        status=LookupStatus.FOUND,
        evidence=tuple(facts),
        facts=tuple(facts),
        limitations=(),
        payload=payload,
    )
    composer = AsyncMock()
    controller = FactCheckController(
        lookup=AsyncMock(return_value=unusable),
        compose=composer,
        max_attempts=1,
        as_of=lambda: "2026-07-31T08:32:15+09:00",
    )

    state = await controller.run(_state())

    assert state.evidence.status is EvidenceStatus.UNUSABLE
    assert "확정할 수 없습니다" in (state.final_text or "")
    composer.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_live_sports_fact_can_enter_composer() -> None:
    result = _result(LookupStatus.FOUND, required_claims=("현재 점수",))
    payload = dict(result.payload)
    fact = dict(result.facts[0])
    fact.update(status="live", winner=None)
    payload.update(
        confidence="medium",
        facts=[fact],
        timeline_validation={"status": "partial"},
    )
    live = RealtimeLookupResult(
        request=result.request,
        status=LookupStatus.FOUND,
        evidence=(fact,),
        facts=(fact,),
        limitations=("경기 진행 중",),
        payload=payload,
    )
    composer = AsyncMock(return_value="진행 중입니다.")
    controller = FactCheckController(
        lookup=AsyncMock(return_value=live),
        compose=composer,
        max_attempts=1,
        as_of=lambda: "2026-07-31T08:32:15+09:00",
    )

    state = await controller.run(_state(required_claims=("현재 점수",)))

    assert state.evidence.status is EvidenceStatus.VERIFIED
    composer.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_required_claim_never_enters_composer() -> None:
    required_claims = ("승자", "관중 수")
    result = _result(LookupStatus.FOUND, required_claims=required_claims)
    payload = dict(result.payload)
    fact = dict(result.facts[0])
    fact["winner"] = None
    payload["facts"] = [fact]
    unusable = RealtimeLookupResult(
        request=result.request,
        status=LookupStatus.FOUND,
        evidence=(fact,),
        facts=(fact,),
        limitations=(),
        payload=payload,
    )
    composer = AsyncMock()
    controller = FactCheckController(
        lookup=AsyncMock(return_value=unusable),
        compose=composer,
        max_attempts=1,
        as_of=lambda: "2026-07-31T08:32:15+09:00",
    )

    state = await controller.run(_state(required_claims=required_claims))

    assert state.evidence.status is EvidenceStatus.UNUSABLE
    assert "확정할 수 없습니다" in (state.final_text or "")
    composer.assert_not_awaited()
