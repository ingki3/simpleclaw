"""BIZ-491 — Unified TurnPlanner prompt 조립과 structured 요청 테스트."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.planner_catalog import PlannerCatalog
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.agent.turn_plan import (
    UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
    ExecutionMode,
)
from simpleclaw.agent.turn_planner import (
    build_turn_planner_user_prompt,
    plan_turn_with_llm,
)
from simpleclaw.llm.models import LLMResponse


def _response_payload() -> str:
    """structured planner 성공 응답 fixture를 JSON 문자열로 만든다."""
    return json.dumps(
        {
            "context": {
                "relation": "same_thread",
                "use_prior_context": True,
                "selected_turn_ids": ["msg:101"],
                "standalone_question": "SK와 NVIDIA의 오늘 협업 발표를 확인해줘",
                "unresolved_references": [],
                "ignored_context_reason": "",
            },
            "clarification": {
                "required": False,
                "question": "",
                "options": [],
                "reason": "",
            },
            "domains": ["news"],
            "intents": ["realtime_lookup"],
            "fact_check": {
                "required": True,
                "owner": "planner",
                "domain": "news",
                "entities": ["SK", "NVIDIA"],
                "search_query": "SK NVIDIA 오늘 협업 발표",
                "required_claims": ["오늘 발표 내용"],
                "freshness_required": True,
                "reason": "현재 사실",
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
                "reason": "최신 사실 조회",
            },
            "confidence": 0.93,
            "decision_summary": "직전 사용자 질문만 사용해 최신 발표를 확인한다.",
        },
        ensure_ascii=False,
    )


def _candidates() -> ContextCandidateSet:
    """assistant evidence 차단 표식을 포함한 문맥 후보 fixture를 만든다."""
    timestamp = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    candidates = (
        ContextCandidate(
            turn_id="msg:101",
            role="user",
            timestamp=timestamp,
            content="SK와 엔비디아 협업 발표를 정리해줘",
            trust=ContextTrust.USER_INPUT,
        ),
        ContextCandidate(
            turn_id="msg:102",
            role="assistant",
            timestamp=timestamp,
            content="최근 발표는 없다고 보입니다.",
            trust=ContextTrust.ASSISTANT_CONTEXT_ONLY,
        ),
    )
    return ContextCandidateSet(
        candidates=candidates,
        total_chars=sum(len(item.content) for item in candidates),
        truncated=False,
    )


def test_prompt_yaml_contains_required_semantic_guards() -> None:
    """YAML SoT가 문맥·evidence·single mode·catalog·CoT 금지 지시를 포함한다."""
    prompt = load_system_prompt("unified_turn_planner", refresh=True).system_prompt

    assert "selected_turn_ids" in prompt
    assert "assistant history" in prompt
    assert "not factual evidence" in prompt
    assert "execution.mode" in prompt
    assert "exact names from the capability catalog" in prompt
    assert "chain-of-thought" in prompt
    assert "decision_summary" in prompt


def test_user_prompt_assembles_current_context_and_catalog() -> None:
    """현재 질문·ID 후보·catalog fingerprint를 하나의 deterministic JSON으로 조립한다."""
    catalog = PlannerCatalog(assets=(), fingerprint="fingerprint-123")

    rendered = build_turn_planner_user_prompt(
        text="오늘 있었던 발표야. 체크해봐",
        candidates=_candidates(),
        catalog=catalog,
    )
    data = json.loads(rendered)

    assert data["current_user_message"] == "오늘 있었던 발표야. 체크해봐"
    assert data["context_candidates"][0]["id"] == "msg:101"
    assert data["context_candidates"][1]["evidence_eligible"] is False
    assert data["capability_catalog"] == []
    assert data["catalog_fingerprint"] == "fingerprint-123"


@pytest.mark.asyncio
async def test_planner_sends_one_structured_request() -> None:
    """성공 경로는 context/fact/execution을 한 번의 schema 요청으로 반환한다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_response_payload()))
    catalog = PlannerCatalog(assets=(), fingerprint="fingerprint-123")

    plan = await plan_turn_with_llm(
        "오늘 있었던 발표야. 체크해봐",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
    )

    assert plan.execution.mode is ExecutionMode.FACT_CHECK
    assert plan.context.selected_turn_ids == ("msg:101",)
    assert plan.catalog_fingerprint == "fingerprint-123"
    router.send.assert_awaited_once()
    request = router.send.await_args.args[0]
    assert request.route_name == "turn_analysis"
    assert request.response_mime_type == "application/json"
    assert request.response_schema is UNIFIED_TURN_PLAN_RESPONSE_SCHEMA
    assert request.require_structured_output is True
    assert request.tools is None


@pytest.mark.asyncio
async def test_reasoning_hint_is_forwarded_only_when_enabled() -> None:
    """provider-neutral reasoning 설정은 명시적으로 켠 경우에만 요청에 포함한다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_response_payload()))
    catalog = PlannerCatalog(assets=(), fingerprint="fingerprint-123")

    await plan_turn_with_llm(
        "질문",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
        reasoning={"enabled": True, "effort": "medium"},
    )

    request = router.send.await_args.args[0]
    assert request.reasoning == {"enabled": True, "effort": "medium"}
