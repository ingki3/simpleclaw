"""BIZ-491 — Unified TurnPlanner truncated repair·retry·fail-closed 테스트."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.plan_gate import PlanGate
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.turn_plan import ExecutionMode
from simpleclaw.agent.turn_planner import (
    PlannerUnavailable,
    plan_turn_with_llm,
    repair_turn_plan_payload,
)
from simpleclaw.llm.models import LLMResponse, LLMRoute
from simpleclaw.llm.router import LLMRouter


def _valid_payload(*, summary: str = "최신 사실을 확인한다.") -> str:
    """repair/retry 테스트용 완전한 structured payload를 만든다."""
    return json.dumps(
        {
            "context": {
                "relation": "standalone",
                "use_prior_context": False,
                "selected_turn_ids": [],
                "standalone_question": "내일 서울 날씨를 알려줘",
                "unresolved_references": [],
                "ignored_context_reason": "",
            },
            "clarification": {
                "required": False,
                "question": "",
                "options": [],
                "reason": "",
            },
            "domains": ["weather"],
            "intents": ["weather"],
            "fact_check": {
                "required": True,
                "owner": "planner",
                "domain": "weather",
                "entities": ["서울"],
                "search_query": "내일 서울 날씨",
                "required_claims": ["내일 강수와 기온"],
                "freshness_required": True,
                "reason": "예보는 현재 사실",
            },
            "execution": {
                "mode": "fact_check",
                "primary_asset": {
                    "asset_type": "skill",
                    "asset_name": "realtime-lookup-skill",
                },
                "allowed_assets": [
                    {
                        "asset_type": "skill",
                        "asset_name": "realtime-lookup-skill",
                    }
                ],
                "allowed_tools": ["execute_skill"],
                "requires_confirmation": False,
                "reason": "날씨 조회",
            },
            "confidence": 0.96,
            "decision_summary": summary,
        },
        ensure_ascii=False,
    )


def _empty_candidates() -> ContextCandidateSet:
    """문맥 없는 planner 호출 fixture를 만든다."""
    return ContextCandidateSet(candidates=(), total_chars=0, truncated=False)


def _catalog() -> PlannerCatalog:
    """repair/retry 성공 응답이 참조하는 runtime-visible catalog를 만든다."""
    return PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type="native_tool",
                name="execute_skill",
                description="선택 skill 실행",
                domains=(),
                intents=(),
                read_only=True,
                side_effects=False,
                freshness_sensitive=False,
                direct_answer=False,
                requires_confirmation=False,
                output_contract=None,
                declared=True,
                runtime_visible=True,
            ),
            PlannerAsset(
                asset_type="skill",
                name="realtime-lookup-skill",
                description="현재 사실 조회",
                domains=("weather",),
                intents=("weather",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=True,
                direct_answer=True,
                requires_confirmation=False,
                output_contract=None,
                declared=True,
                runtime_visible=True,
            ),
            PlannerAsset(
                asset_type="recipe",
                name="sports-live",
                description="현재 스포츠 결과 exact recipe",
                domains=("sports",),
                intents=("current_result", "ranking"),
                read_only=True,
                side_effects=False,
                freshness_sensitive=True,
                direct_answer=True,
                requires_confirmation=False,
                output_contract="asset_result.v1",
                declared=True,
                runtime_visible=True,
                coverage="full_coverage",
                input_contract="query.v1",
            ),
        ),
        fingerprint="catalog-v1",
    )


def _valid_exact_sports_payload() -> str:
    """실제 LPGA 입력의 exact sports-live structured 계획을 만든다."""
    data = json.loads(_valid_payload())
    data["context"]["standalone_question"] = (
        "어제 유해란 LPGA 1라운드 성적과 순위를 현재 공식 결과로 확인해줘"
    )
    data["domains"] = ["sports"]
    data["intents"] = ["current_result", "ranking"]
    data["fact_check"] = {
        "required": True,
        "owner": "planner",
        "domain": "sports",
        "entities": ["유해란", "LPGA"],
        "reference_date": "2026-08-02",
        "search_query": "유해란 LPGA 1라운드 공식 결과 순위",
        "required_claims": ["1라운드 성적", "순위"],
        "freshness_required": True,
        "reason": "공식 현재 결과 확인",
    }
    data["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {
            "asset_type": "recipe",
            "asset_name": "sports-live",
        },
        "supporting_assets": [],
        "fallback_modes": ["answer_with_evidence"],
        "reason": "exact recipe",
    }
    data["execution"] = {
        "mode": "answer_with_evidence",
        "allowed_tools": ["execute_skill"],
        "requires_confirmation": False,
        "complexity_signals": [],
        "reason": "recipe owns its delegate",
    }
    return json.dumps(data, ensure_ascii=False)


def test_repair_recovers_payload_cut_inside_decision_summary() -> None:
    """마지막 설명 문자열 truncation은 핵심 계획을 보존한 채 복구한다."""
    payload = _valid_payload(summary="이 설명은 출력 토큰 경계에서 잘")
    truncated = payload[:-4]

    plan = repair_turn_plan_payload(
        truncated,
        original_text="내일 서울 날씨는?",
        catalog_fingerprint="catalog-v1",
    )

    assert plan is not None
    assert plan.execution.mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    assert plan.fact_check.search_query == "내일 서울 날씨"
    assert plan.decision_summary == ""


def test_repair_rejects_payload_cut_before_execution() -> None:
    """execution 이전 truncation은 실행 결정을 지어내지 않고 거부한다."""
    payload = _valid_payload()
    truncated = payload.split('"execution"')[0]

    assert repair_turn_plan_payload(
        truncated,
        original_text="내일 서울 날씨는?",
    ) is None


@pytest.mark.asyncio
async def test_planner_repairs_truncated_tail_without_retry() -> None:
    """repair 가능한 설명 tail은 같은 provider 호출 한 번으로 계획을 확정한다."""
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(
            text=_valid_payload(summary="출력 끝에서 잘린 설명")[:-4],
            finish_reason="length",
        )
    )

    plan = await plan_turn_with_llm(
        "내일 서울 날씨는?",
        candidates=_empty_candidates(),
        catalog=_catalog(),
        router=router,
    )

    assert plan.execution.mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    router.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_planner_retries_route_once_after_parse_and_repair_fail() -> None:
    """primary malformed 응답은 route retry 한 번 뒤 성공 계획을 반환한다."""
    primary = AsyncMock()
    primary.send = AsyncMock(return_value=LLMResponse(text="not-json"))
    retry = AsyncMock()
    retry.send = AsyncMock(return_value=LLMResponse(text=_valid_payload()))
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        default_backend="primary",
        routes={
            "turn_analysis": LLMRoute(
                "turn_analysis",
                "primary",
                "retry",
            )
        },
    )

    plan = await plan_turn_with_llm(
        "내일 서울 날씨는?",
        candidates=_empty_candidates(),
        catalog=_catalog(),
        router=router,
    )

    assert plan.execution.mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    primary.send.assert_awaited_once()
    retry.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_output_cap_retries_same_route_once_with_minimal_reasoning() -> None:
    """reasoning이 cap을 잠식하면 동일 cap에서 최소 reasoning으로 한 번 복구한다."""
    provider = AsyncMock()
    provider.send = AsyncMock(
        side_effect=[
            LLMResponse(text='{"context":', finish_reason="length"),
            LLMResponse(text=_valid_exact_sports_payload(), finish_reason="stop"),
        ]
    )
    router = LLMRouter(
        backends={},
        providers={"primary": provider},
        default_backend="primary",
        routes={"turn_analysis": LLMRoute("turn_analysis", "primary")},
    )

    plan = await plan_turn_with_llm(
        "어제 유해란 LPGA 1라운드 성적과 순위를 현재 공식 결과로 확인해줘",
        candidates=_empty_candidates(),
        catalog=_catalog(),
        router=router,
        max_tokens=2048,
        reasoning={"enabled": True, "effort": "medium", "budget_tokens": 512},
    )

    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "sports-live"
    assert plan.execution.allowed_tools == ()
    assert plan.fact_check.owner.value == "asset"
    assert (
        PlanGate()
        .evaluate(
            plan,
            candidates=_empty_candidates(),
            catalog=_catalog(),
        )
        .status.value
        == "pass"
    )
    assert provider.send.await_count == 2
    first_call, retry_call = provider.send.await_args_list
    assert first_call.kwargs["max_tokens"] == 2048
    assert retry_call.kwargs["max_tokens"] == 2048
    assert first_call.kwargs["reasoning"]["enabled"] is True
    assert retry_call.kwargs["reasoning"] == {
        "enabled": True,
        "effort": "minimal",
    }


@pytest.mark.asyncio
async def test_output_cap_twice_is_stable_fail_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """primary/retry cap은 세 번째 시도 없이 원문 비기록 fail-closed한다."""
    private_body = "PRIVATE_CAPPED_PLANNER_BODY"
    private_user = "PRIVATE_CAPPED_USER_TEXT"
    provider = AsyncMock()
    provider.send = AsyncMock(
        side_effect=[
            LLMResponse(text=private_body, finish_reason="length"),
            LLMResponse(text=private_body, finish_reason="length"),
        ]
    )
    router = LLMRouter(
        backends={},
        providers={"primary": provider},
        default_backend="primary",
        routes={"turn_analysis": LLMRoute("turn_analysis", "primary")},
    )

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(PlannerUnavailable, match="unavailable"),
    ):
        await plan_turn_with_llm(
            private_user,
            candidates=_empty_candidates(),
            catalog=_catalog(),
            router=router,
            reasoning={"enabled": True, "budget_tokens": 512},
        )

    assert provider.send.await_count == 2
    assert private_body not in caplog.text
    assert private_user not in caplog.text
    assert "finish_reason=length" in caplog.text


@pytest.mark.asyncio
async def test_semantic_boundary_failure_consumes_route_retry() -> None:
    """shape-valid hallucinated asset도 validation 실패 후 route retry로 복구한다."""
    invalid = json.loads(_valid_payload())
    invalid["execution"]["primary_asset"] = {
        "asset_type": "skill",
        "asset_name": "invented-skill",
    }
    invalid["execution"]["allowed_assets"] = [
        {
            "asset_type": "skill",
            "asset_name": "invented-skill",
        }
    ]
    primary = AsyncMock()
    primary.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(invalid, ensure_ascii=False))
    )
    retry = AsyncMock()
    retry.send = AsyncMock(return_value=LLMResponse(text=_valid_payload()))
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        default_backend="primary",
        routes={
            "turn_analysis": LLMRoute(
                "turn_analysis",
                "primary",
                "retry",
            )
        },
    )

    plan = await plan_turn_with_llm(
        "내일 서울 날씨는?",
        candidates=_empty_candidates(),
        catalog=_catalog(),
        router=router,
    )

    assert plan.execution.primary_asset is not None
    assert plan.execution.primary_asset.name == "realtime-lookup-skill"
    primary.send.assert_awaited_once()
    retry.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_planner_raises_unavailable_after_retry_without_semantic_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """두 provider가 모두 실패하면 keyword route 없이 PlannerUnavailable을 던진다."""
    primary_marker = "PRIVATE_PRIMARY_PLANNER_BODY"
    retry_marker = "PRIVATE_RETRY_PLANNER_BODY"
    user_marker = "PRIVATE_USER_PLANNER_TEXT"
    primary = AsyncMock()
    primary.send = AsyncMock(return_value=LLMResponse(text=primary_marker))
    retry = AsyncMock()
    retry.send = AsyncMock(return_value=LLMResponse(text=retry_marker))
    router = LLMRouter(
        backends={},
        providers={"primary": primary, "retry": retry},
        default_backend="primary",
        routes={
            "turn_analysis": LLMRoute(
                "turn_analysis",
                "primary",
                "retry",
            )
        },
    )

    with (
        caplog.at_level(logging.WARNING, logger="simpleclaw.agent.turn_planner"),
        pytest.raises(PlannerUnavailable, match="unavailable"),
    ):
        await plan_turn_with_llm(
            user_marker,
            candidates=_empty_candidates(),
            catalog=_catalog(),
            router=router,
        )

    primary.send.assert_awaited_once()
    retry.send.assert_awaited_once()
    assert primary_marker not in caplog.text
    assert retry_marker not in caplog.text
    assert user_marker not in caplog.text
    assert "repair_status=failed" in caplog.text


@pytest.mark.asyncio
async def test_lightweight_router_failure_is_fail_closed_without_keyword_fallback() -> None:
    """legacy fake router도 invalid JSON을 보수적 계획으로 위조하지 않는다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text="plain text"))

    with pytest.raises(PlannerUnavailable):
        await plan_turn_with_llm(
            "현재 삼성전자 주가는?",
            candidates=_empty_candidates(),
            catalog=_catalog(),
            router=router,
        )

    router.send.assert_awaited_once()
