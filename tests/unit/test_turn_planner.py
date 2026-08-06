"""BIZ-491 — Unified TurnPlanner prompt 조립과 structured 요청 테스트."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import (
    ContextCandidate,
    ContextCandidateSet,
    ContextTrust,
)
from simpleclaw.agent.plan_gate import PlanGate
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.agent.turn_plan import (
    UNIFIED_TURN_PLAN_RESPONSE_SCHEMA,
    ExecutionMode,
)
from simpleclaw.agent.turn_planner import (
    PlannerUnavailable,
    build_turn_planner_user_prompt,
    plan_turn_with_llm,
)
from simpleclaw.llm.models import LLMResponse, LLMRoute
from simpleclaw.llm.router import LLMRouter


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


def _catalog(
    *,
    side_effecting_skill: bool = False,
    skill_runtime_visible: bool = True,
) -> PlannerCatalog:
    """성공·side-effect·internal 경계를 표현하는 compact catalog를 만든다."""
    skill = PlannerAsset(
        asset_type="skill",
        name="realtime-lookup-skill",
        description="현재 사실 조회",
        domains=("news",),
        intents=("realtime_lookup",),
        read_only=not side_effecting_skill,
        side_effects=side_effecting_skill,
        freshness_sensitive=True,
        direct_answer=True,
        requires_confirmation=side_effecting_skill,
        output_contract=None,
        declared=True,
        runtime_visible=skill_runtime_visible,
    )
    execute_skill = PlannerAsset(
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
    )
    return PlannerCatalog(
        assets=(execute_skill, skill),
        fingerprint="fingerprint-123",
    )


def _exact_recipe_catalog() -> PlannerCatalog:
    """중복 delegate narrowing을 허용하는 typed exact recipe catalog."""
    return PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type="native_tool",
                name="execute_skill",
                description="selected skill delegate",
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
                asset_type="native_tool",
                name="web_search",
                description="runtime-visible read-only web search",
                domains=("news",),
                intents=("realtime_lookup",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=True,
                direct_answer=False,
                requires_confirmation=False,
                output_contract=None,
                declared=True,
                runtime_visible=True,
            ),
            PlannerAsset(
                asset_type="recipe",
                name="sports-live",
                description="typed sports result recipe",
                domains=("sports",),
                intents=("current_result",),
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
                delegated_skills=("naver-sports-skill",),
                contract_owner="recipe:sports-live",
                input_contract_ref="recipe.sports-live.input@1",
                output_contract_ref="recipe.sports-live.output@1",
                input_schema_hash="i" * 64,
                output_schema_hash="o" * 64,
                binding_identity="binding:" + "b" * 64,
                definition_fingerprint="r" * 64,
            ),
            PlannerAsset(
                asset_type="skill",
                name="naver-sports-skill",
                description="recipe-owned read-only sports delegate",
                domains=("sports",),
                intents=("current_result",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=True,
                direct_answer=True,
                requires_confirmation=False,
                output_contract="asset_result.v1",
                declared=True,
                runtime_visible=True,
                contract_owner="skill:naver-sports-skill",
                input_contract_ref="skill.naver-sports-skill.input@1",
                output_contract_ref="skill.naver-sports-skill.output@1",
                input_schema_hash="i" * 64,
                output_schema_hash="o" * 64,
                binding_identity="binding:" + "b" * 64,
                definition_fingerprint="s" * 64,
            ),
            PlannerAsset(
                asset_type="skill",
                name="read-private-mail",
                description="unrelated read-only mail skill",
                domains=("email",),
                intents=("read",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=False,
                direct_answer=True,
                requires_confirmation=False,
                output_contract="asset_result.v1",
                declared=True,
                runtime_visible=True,
            ),
        ),
        fingerprint="sports-live-catalog",
    )


def _exact_recipe_response_data() -> dict[str, object]:
    data = _response_data()
    data["context"] = {
        "relation": "standalone",
        "use_prior_context": False,
        "selected_turn_ids": [],
        "standalone_question": "어제 유해란 LPGA 성적과 순위",
        "unresolved_references": [],
        "ignored_context_reason": "",
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
        "reason": "fallback only",
    }
    return data


def _response_data() -> dict[str, object]:
    """성공 응답 JSON을 trust-boundary 변형 가능한 dict로 반환한다."""
    data = json.loads(_response_payload())
    assert isinstance(data, dict)
    return data


def test_prompt_yaml_contains_required_semantic_guards() -> None:
    """YAML SoT가 문맥·evidence·single mode·catalog·CoT 금지 지시를 포함한다."""
    prompt = load_system_prompt("unified_turn_planner", refresh=True).system_prompt

    assert "selected_turn_ids" in prompt
    assert "assistant history" in prompt
    assert "not factual evidence" in prompt
    assert "execution.mode" in prompt
    assert "exact names from the capability catalog" in prompt
    assert "Never duplicate capability.primary_asset" in prompt
    assert "chain-of-thought" in prompt
    assert "decision_summary" in prompt
    assert "Populate every required schema field exactly once" in prompt
    assert "preserve the complete current_user_message" in prompt
    assert "full_coverage Recipe must use execution.mode=direct_answer" in prompt
    assert "This Recipe exception does not apply to a Skill" in prompt
    assert "execution.allowed_tools includes" in prompt
    assert "execute_skill" in prompt
    assert "fresh evidence must be combined with an open-ended" in prompt
    assert "confirmation question is not missing information" in prompt
    assert "current score or result explicit" in prompt
    assert "matching Recipe name or description without both contracts" in prompt
    assert "smallest deterministic set of exact assets" in prompt
    assert "price lookup plus bounded configuration advice" in prompt
    assert "Pending confirmation never means capability.coverage=needs_input" in prompt
    assert "private account state" in prompt

    examples = load_system_prompt(
        "unified_turn_planner_examples", refresh=True
    ).field("template")
    assert "bounded tool-iterative ReAct" in examples
    assert "multi-source Deep Research" in examples
    assert "fresh facts plus practical judgment" in examples
    assert "facts separated from interpretation" in examples
    assert "implicit live-game status" in examples
    assert "context-free acknowledgement" in examples
    assert "confirmed-before-dispatch mutation" in examples
    assert "one named product with bounded configuration advice" in examples
    assert "complex market request with an uncontracted routine Recipe" in examples
    assert "private account read" in examples
    assert "link claim follow-up" in examples


def test_unified_schema_keeps_canonical_modes_and_typed_fact_contract() -> None:
    properties = UNIFIED_TURN_PLAN_RESPONSE_SCHEMA["properties"]

    assert properties["execution"]["properties"]["mode"]["enum"] == [
        "clarify",
        "direct_answer",
        "answer_with_evidence",
        "resolve_complex_problem",
    ]
    fact_properties = properties["fact_check"]["properties"]
    assert {
        "domain",
        "intents",
        "entities",
        "reference_date",
        "search_query",
        "required_claims",
        "freshness_required",
    }.issubset(fact_properties)
    assert fact_properties["entities"]["items"]["required"] == ["kind", "value"]
    assert properties["context"]["properties"]["selected_turn_ids"]["maxItems"] > 0
    assert properties["capability"]["properties"]["fallback_modes"]["maxItems"] == 4


def test_user_prompt_assembles_current_context_and_catalog() -> None:
    """현재 질문·ID 후보·catalog fingerprint를 하나의 deterministic JSON으로 조립한다."""
    catalog = PlannerCatalog(assets=(), fingerprint="fingerprint-123")

    rendered = build_turn_planner_user_prompt(
        text="오늘 있었던 발표야. 체크해봐",
        candidates=_candidates(),
        catalog=catalog,
        current_kst_date="2026-08-03",
    )
    data = json.loads(rendered)

    assert data["current_user_message"] == "오늘 있었던 발표야. 체크해봐"
    assert data["planner_clock"] == {
        "current_date": "2026-08-03",
        "timezone": "Asia/Seoul",
    }
    assert data["context_candidates"][0]["id"] == "msg:101"
    assert data["context_candidates"][1]["evidence_eligible"] is False
    assert data["capability_catalog"] == []
    assert data["catalog_fingerprint"] == "fingerprint-123"


@pytest.mark.asyncio
async def test_planner_sends_one_structured_request() -> None:
    """성공 경로는 context/fact/execution을 한 번의 schema 요청으로 반환한다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_response_payload()))
    catalog = _catalog()

    plan = await plan_turn_with_llm(
        "오늘 있었던 발표야. 체크해봐",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
    )

    assert plan.execution.mode is ExecutionMode.ANSWER_WITH_EVIDENCE
    assert plan.context.selected_turn_ids == ("msg:101",)
    assert plan.catalog_fingerprint == "fingerprint-123"
    router.send.assert_awaited_once()
    request = router.send.await_args.args[0]
    assert request.route_name == "turn_analysis"
    assert request.response_mime_type == "application/json"
    assert request.response_schema is not UNIFIED_TURN_PLAN_RESPONSE_SCHEMA
    allowed_tool_schema = request.response_schema["properties"]["execution"][
        "properties"
    ]["allowed_tools"]
    assert allowed_tool_schema["items"]["enum"] == ["execute_skill"]
    assert allowed_tool_schema["maxItems"] == 1
    assert request.require_structured_output is True
    assert request.tools is None


@pytest.mark.asyncio
async def test_reasoning_hint_is_forwarded_only_when_enabled() -> None:
    """provider-neutral reasoning 설정은 명시적으로 켠 경우에만 요청에 포함한다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_response_payload()))
    catalog = _catalog()

    await plan_turn_with_llm(
        "질문",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
        reasoning={"enabled": True, "effort": "medium"},
    )

    request = router.send.await_args.args[0]
    assert request.reasoning == {"enabled": True, "effort": "medium"}


@pytest.mark.asyncio
async def test_empty_boundaries_reject_hallucinated_ids_assets_and_tools(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """빈 후보/catalog에서는 모델이 만든 ID·asset·tool을 모두 fail-closed한다."""
    data = _response_data()
    data["context"]["selected_turn_ids"] = ["invented-turn-id"]
    data["execution"] = {
        "mode": "execute_asset",
        "primary_asset": {
            "asset_type": "skill",
            "asset_name": "invented-dangerous-skill",
        },
        "allowed_assets": [
            {
                "asset_type": "skill",
                "asset_name": "invented-dangerous-skill",
            }
        ],
        "allowed_tools": ["invented_tool"],
        "requires_confirmation": False,
        "reason": "hallucinated scope",
    }
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    with (
        caplog.at_level(logging.WARNING, logger="simpleclaw.agent.turn_planner"),
        pytest.raises(PlannerUnavailable),
    ):
        await plan_turn_with_llm(
            "질문",
            candidates=ContextCandidateSet(
                candidates=(),
                total_chars=0,
                truncated=False,
            ),
            catalog=PlannerCatalog(assets=(), fingerprint="empty"),
            router=router,
        )

    assert "boundary_code=unknown_selected_turn_id" in caplog.text
    assert "invented-turn-id" not in caplog.text
    assert "invented-dangerous-skill" not in caplog.text
    assert "invented_tool" not in caplog.text


@pytest.mark.asyncio
async def test_legacy_primary_asset_must_also_be_in_allowed_assets() -> None:
    """legacy execution-only primary는 기존 allowed_assets 경계를 유지한다."""
    data = _response_data()
    data["execution"]["allowed_assets"] = []
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    with pytest.raises(PlannerUnavailable):
        await plan_turn_with_llm(
            "질문",
            candidates=_candidates(),
            catalog=_catalog(),
            router=router,
        )


@pytest.mark.asyncio
async def test_capability_primary_does_not_require_supporting_duplicate() -> None:
    """capability-native full primary는 빈 supporting fallback과 독립 검증한다."""
    data = _response_data()
    data["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {
            "asset_type": "skill",
            "asset_name": "realtime-lookup-skill",
        },
        "supporting_assets": [],
        "fallback_modes": ["answer_with_evidence"],
        "reason": "exact asset owns the request",
    }
    data["execution"] = {
        "mode": "answer_with_evidence",
        "allowed_tools": [],
        "requires_confirmation": False,
        "complexity_signals": [],
        "reason": "fallback only",
    }
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    plan = await plan_turn_with_llm(
        "질문",
        candidates=_candidates(),
        catalog=_catalog(),
        router=router,
    )

    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "realtime-lookup-skill"
    assert plan.capability.supporting_assets == ()


@pytest.mark.asyncio
async def test_standalone_question_preserves_original_terms_and_provider_expansion() -> None:
    """독립 질문은 원문 표현과 provider의 실행 단서를 함께 보존한다."""
    data = _response_data()
    data["context"]["relation"] = "standalone"
    data["context"]["use_prior_context"] = False
    data["context"]["selected_turn_ids"] = []
    data["context"]["standalone_question"] = "오늘 롯데 선발 예정 선수를 확인해줘"
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "오늘 롯데 선발투수 누구지?",
        candidates=_candidates(),
        catalog=_catalog(),
        router=router,
    )

    assert "오늘 롯데 선발투수 누구지?" in plan.context.standalone_question
    assert "선발 예정 선수" in plan.context.standalone_question


@pytest.mark.asyncio
async def test_full_skill_primary_narrows_redundant_read_only_supporting_scope() -> None:
    """declared full Skill은 중복 collector에 따라 asset set이 흔들리지 않는다."""
    primary = PlannerAsset(
        asset_type="skill",
        name="news-skill",
        description="multi-source news evidence",
        domains=("news",),
        intents=("realtime_lookup",),
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
    )
    supporting = PlannerAsset(
        asset_type="native_tool",
        name="web_search",
        description="generic search",
        domains=("news",),
        intents=("realtime_lookup",),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
        direct_answer=False,
        requires_confirmation=False,
        output_contract="asset_result.v1",
        declared=True,
        runtime_visible=True,
        coverage="partial_coverage",
        input_contract="query.v1",
    )
    adapter = PlannerAsset(
        asset_type="native_tool",
        name="execute_skill",
        description="selected Skill adapter",
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
    )
    catalog = PlannerCatalog((primary, supporting, adapter), "full-skill-catalog")
    data = _response_data()
    data["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {"asset_type": "skill", "asset_name": "news-skill"},
        "supporting_assets": [
            {"asset_type": "native_tool", "asset_name": "web_search"}
        ],
        "fallback_modes": [],
        "reason": "news skill owns the whole request",
    }
    data["execution"] = {
        "mode": "resolve_complex_problem",
        "allowed_tools": ["execute_skill", "web_search"],
        "requires_confirmation": False,
        "complexity_signals": ["ordered_capability_composition"],
        "reason": "analyze grounded evidence",
    }
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "발표와 영향을 여러 출처로 검증해줘",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
    )

    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "news-skill"
    assert plan.capability.supporting_assets == ()
    assert plan.execution.allowed_tools == ("execute_skill",)


def _full_skill_scope_asset(
    asset_type: str,
    name: str,
    *,
    coverage: str = "partial_coverage",
    read_only: bool = True,
    side_effects: bool = False,
    requires_confirmation: bool = False,
    runtime_visible: bool = True,
) -> PlannerAsset:
    """full Skill 정규화 경계용 compact catalog asset을 만든다."""
    is_skill = asset_type == "skill"
    return PlannerAsset(
        asset_type=asset_type,
        name=name,
        description=name,
        domains=("news",),
        intents=("realtime_lookup",),
        read_only=read_only,
        side_effects=side_effects,
        freshness_sensitive=False,
        direct_answer=is_skill,
        requires_confirmation=requires_confirmation,
        output_contract="asset_result.v1" if is_skill else None,
        declared=True,
        runtime_visible=runtime_visible,
        coverage=coverage,
        input_contract="query.v1" if is_skill else None,
    )


def _full_skill_scope_data(*, allowed_tools: list[str]) -> dict[str, object]:
    """full Skill + supporting collector provider 응답을 만든다."""
    data = _response_data()
    data["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {"asset_type": "skill", "asset_name": "news-skill"},
        "supporting_assets": [
            {"asset_type": "native_tool", "asset_name": "web_search"}
        ],
        "fallback_modes": [],
        "reason": "news skill owns the whole request",
    }
    data["execution"] = {
        "mode": "resolve_complex_problem",
        "allowed_tools": allowed_tools,
        "requires_confirmation": False,
        "complexity_signals": ["ordered_capability_composition"],
        "reason": "compose selected scope",
    }
    return data


def _full_skill_scope_catalog(
    *extra_tools: PlannerAsset,
) -> PlannerCatalog:
    """full Skill, adapter, collector와 선택적 경계 tool catalog를 만든다."""
    return PlannerCatalog(
        assets=(
            _full_skill_scope_asset(
                "skill", "news-skill", coverage="full_coverage"
            ),
            _full_skill_scope_asset("native_tool", "web_search"),
            _full_skill_scope_asset("native_tool", "execute_skill"),
            *extra_tools,
        ),
        fingerprint="full-skill-scope-catalog",
    )


@pytest.mark.asyncio
async def test_native_collectors_remove_unselected_skill_adapter() -> None:
    """exact read-only native scope는 미선택 Skill adapter 없이 안정화한다."""
    data = _response_data()
    data["capability"] = {
        "coverage": "partial_coverage",
        "primary_asset": {"asset_type": "none", "asset_name": "__none__"},
        "supporting_assets": [
            {"asset_type": "native_tool", "asset_name": "web_search"},
            {"asset_type": "native_tool", "asset_name": "web_fetch"},
        ],
        "fallback_modes": [],
        "reason": "two exact native collectors",
    }
    data["execution"] = {
        "mode": "resolve_complex_problem",
        "allowed_tools": ["execute_skill", "web_search", "web_fetch"],
        "requires_confirmation": False,
        "complexity_signals": ["ordered_capability_composition"],
        "reason": "compose evidence",
    }
    catalog = PlannerCatalog(
        assets=(
            _full_skill_scope_asset("native_tool", "execute_skill"),
            _full_skill_scope_asset("native_tool", "web_search"),
            _full_skill_scope_asset("native_tool", "web_fetch"),
        ),
        fingerprint="native-collector-catalog",
    )
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "여러 출처로 검증해줘",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
    )

    assert tuple(asset.name for asset in plan.capability.supporting_assets) == (
        "web_search",
        "web_fetch",
    )
    assert plan.execution.allowed_tools == ("web_search", "web_fetch")


@pytest.mark.asyncio
async def test_native_collector_adapter_normalization_preserves_mutation_scope() -> None:
    """mutation/confirmation native scope에서는 adapter를 임의 제거하지 않는다."""
    data = _response_data()
    data["capability"] = {
        "coverage": "partial_coverage",
        "primary_asset": {"asset_type": "none", "asset_name": "__none__"},
        "supporting_assets": [
            {"asset_type": "native_tool", "asset_name": "send_calendar_invite"}
        ],
        "fallback_modes": [],
        "reason": "mutation scope",
    }
    data["execution"] = {
        "mode": "direct_answer",
        "allowed_tools": ["execute_skill", "send_calendar_invite"],
        "requires_confirmation": False,
        "complexity_signals": [],
        "reason": "confirm before dispatch",
    }
    mutation = _full_skill_scope_asset(
        "native_tool",
        "send_calendar_invite",
        read_only=False,
        side_effects=True,
        requires_confirmation=True,
    )
    catalog = PlannerCatalog(
        assets=(
            _full_skill_scope_asset("native_tool", "execute_skill"),
            mutation,
        ),
        fingerprint="mutation-native-catalog",
    )
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "초대장을 보내줘",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
    )

    assert plan.execution.allowed_tools == (
        "execute_skill",
        "send_calendar_invite",
    )
    assert plan.execution.requires_confirmation is True


@pytest.mark.parametrize(
    ("tool_name", "side_effects", "requires_confirmation"),
    (
        ("delete_calendar_event", True, True),
        ("send_calendar_invite", False, True),
    ),
)
@pytest.mark.asyncio
async def test_full_skill_normalization_preserves_confirmation_tool(
    tool_name: str,
    side_effects: bool,
    requires_confirmation: bool,
) -> None:
    """mutation/confirmation tool은 삭제하지 않고 confirmation 경계로 보낸다."""
    selected_tool = _full_skill_scope_asset(
        "native_tool",
        tool_name,
        read_only=not side_effects,
        side_effects=side_effects,
        requires_confirmation=requires_confirmation,
    )
    data = _full_skill_scope_data(
        allowed_tools=["execute_skill", "web_search", tool_name]
    )
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "뉴스를 확인해줘",
        candidates=_candidates(),
        catalog=_full_skill_scope_catalog(selected_tool),
        router=router,
    )

    assert plan.capability.supporting_assets[0].name == "web_search"
    assert plan.execution.allowed_tools == ("execute_skill", "web_search", tool_name)
    assert tool_name in plan.execution.allowed_tools
    assert plan.execution.requires_confirmation is True


@pytest.mark.asyncio
async def test_full_skill_normalization_rejects_unrelated_read_only_tool() -> None:
    """관련 없는 read-only tool은 safe adapter scope로 세탁하지 않는다."""
    unrelated = _full_skill_scope_asset("native_tool", "read_private_mail")
    data = _full_skill_scope_data(
        allowed_tools=["execute_skill", "web_search", unrelated.name]
    )
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "뉴스를 확인해줘",
            candidates=_candidates(),
            catalog=_full_skill_scope_catalog(unrelated),
            router=router,
        )

    assert exc_info.value.boundary_code == "unrelated_full_skill_tool"


@pytest.mark.asyncio
async def test_full_skill_normalization_rejects_unknown_tool() -> None:
    """unknown/internal tool은 adapter 단일 scope로 덮어쓰지 않는다."""
    data = _full_skill_scope_data(
        allowed_tools=["execute_skill", "web_search", "internal_operator_tool"]
    )
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "뉴스를 확인해줘",
            candidates=_candidates(),
            catalog=_full_skill_scope_catalog(),
            router=router,
        )

    assert exc_info.value.boundary_code == "unknown_or_internal_tool"


@pytest.mark.asyncio
async def test_first_supporting_full_skill_is_promoted_to_stable_primary() -> None:
    """partial/none으로 오표기된 첫 full Skill은 단일 primary로 복원한다."""
    primary = PlannerAsset(
        asset_type="skill",
        name="news-skill",
        description="multi-source news evidence",
        domains=("news",),
        intents=("realtime_lookup",),
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
    )
    collector = PlannerAsset(
        asset_type="native_tool",
        name="web_search",
        description="generic search",
        domains=("news",),
        intents=("realtime_lookup",),
        read_only=True,
        side_effects=False,
        freshness_sensitive=True,
        direct_answer=False,
        requires_confirmation=False,
        output_contract="asset_result.v1",
        declared=True,
        runtime_visible=True,
        coverage="partial_coverage",
        input_contract="query.v1",
    )
    adapter = PlannerAsset(
        asset_type="native_tool",
        name="execute_skill",
        description="selected Skill adapter",
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
    )
    data = _response_data()
    data["capability"] = {
        "coverage": "partial_coverage",
        "primary_asset": {
            "asset_type": "none",
            "asset_name": "__none__",
        },
        "supporting_assets": [
            {"asset_type": "skill", "asset_name": "news-skill"},
            {"asset_type": "native_tool", "asset_name": "web_search"},
        ],
        "fallback_modes": ["answer_with_evidence"],
        "reason": "news skill and generic collectors can gather evidence",
    }
    data["execution"] = {
        "mode": "resolve_complex_problem",
        "allowed_tools": ["execute_skill", "web_search"],
        "requires_confirmation": False,
        "complexity_signals": ["ordered_capability_composition"],
        "reason": "analyze grounded evidence",
    }
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "발표와 영향을 여러 출처로 검증해줘",
        candidates=_candidates(),
        catalog=PlannerCatalog((primary, collector, adapter), "full-skill-catalog"),
        router=router,
    )

    assert plan.capability.coverage.value == "full_coverage"
    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "news-skill"
    assert plan.capability.supporting_assets == ()
    assert plan.execution.allowed_tools == ("execute_skill",)


@pytest.mark.asyncio
async def test_partial_labeled_full_skill_primary_is_stabilized() -> None:
    """partial 오표기된 exact full Skill primary도 단일 scope로 복원한다."""
    data = _full_skill_scope_data(
        allowed_tools=["execute_skill", "web_search"]
    )
    data["capability"]["coverage"] = "partial_coverage"
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "발표와 영향을 여러 출처로 검증해줘",
        candidates=_candidates(),
        catalog=_full_skill_scope_catalog(),
        router=router,
    )

    assert plan.capability.coverage.value == "full_coverage"
    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "news-skill"
    assert plan.capability.supporting_assets == ()
    assert plan.execution.allowed_tools == ("execute_skill",)


@pytest.mark.asyncio
async def test_partial_skill_primary_moves_to_stable_supporting_order() -> None:
    """contract-partial Skill primary는 identity를 보존해 supporting 선두로 이동한다."""
    data = _full_skill_scope_data(
        allowed_tools=["execute_skill", "web_search"]
    )
    data["capability"]["coverage"] = "partial_coverage"
    catalog = PlannerCatalog(
        assets=(
            _full_skill_scope_asset("skill", "news-skill"),
            _full_skill_scope_asset("native_tool", "web_search"),
            _full_skill_scope_asset("native_tool", "execute_skill"),
        ),
        fingerprint="partial-skill-catalog",
    )
    router = AsyncMock(
        send=AsyncMock(
            return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
        )
    )

    plan = await plan_turn_with_llm(
        "발표와 영향을 여러 출처로 검증해줘",
        candidates=_candidates(),
        catalog=catalog,
        router=router,
    )

    assert plan.capability.primary_asset is None
    assert tuple(asset.name for asset in plan.capability.supporting_assets) == (
        "news-skill",
        "web_search",
    )
    assert plan.execution.allowed_tools == ("execute_skill", "web_search")


@pytest.mark.parametrize(
    "allowed_tools",
    ([], ["execute_skill"], ["naver-sports-skill"]),
)
@pytest.mark.asyncio
async def test_exact_recipe_safe_top_level_scope_is_narrowed(
    allowed_tools: list[str],
) -> None:
    """recipe의 빈 scope와 중복 delegate는 안전한 owner로 축소한다."""
    data = _exact_recipe_response_data()
    data["execution"]["allowed_tools"] = allowed_tools
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(data, ensure_ascii=False)
        )
    )

    plan = await plan_turn_with_llm(
        "어제 유해란 LPGA 성적과 순위",
        candidates=ContextCandidateSet(candidates=(), total_chars=0, truncated=False),
        catalog=_exact_recipe_catalog(),
        router=router,
    )

    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "sports-live"
    assert plan.capability.supporting_assets == ()
    assert plan.execution.allowed_tools == ()
    assert plan.fact_check.owner.value == "asset"
    assert (
        PlanGate()
        .evaluate(
            plan,
            candidates=ContextCandidateSet(
                candidates=(), total_chars=0, truncated=False
            ),
            catalog=_exact_recipe_catalog(),
        )
        .status.value
        == "pass"
    )


@pytest.mark.asyncio
async def test_exact_recipe_unrelated_top_level_tool_is_not_narrowed() -> None:
    """exact recipe라도 중복 delegate 이외의 top-level tool은 fail-closed한다."""
    data = _exact_recipe_response_data()
    data["execution"]["allowed_tools"] = ["web_search"]
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    candidates = ContextCandidateSet(
        candidates=(), total_chars=0, truncated=False
    )
    catalog = _exact_recipe_catalog()
    plan = await plan_turn_with_llm(
        "어제 유해란 LPGA 성적과 순위",
        candidates=candidates,
        catalog=catalog,
        router=router,
    )

    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.name == "sports-live"
    assert plan.capability.supporting_assets == ()
    assert plan.execution.allowed_tools == ("web_search",)
    assert plan.fact_check.owner.value == "planner"
    gate_result = PlanGate().evaluate(
        plan,
        candidates=candidates,
        catalog=catalog,
    )
    assert gate_result.status.value == "repair"
    assert {
        violation.code for violation in gate_result.violations
    } >= {"asset.full_coverage_recipe_has_top_level_tools"}


@pytest.mark.asyncio
async def test_exact_recipe_unrelated_read_only_skill_is_not_narrowed() -> None:
    """unrelated read-only Skill은 Recipe delegate로 세탁하지 않고 거부한다."""
    data = _exact_recipe_response_data()
    data["execution"]["allowed_tools"] = ["read-private-mail"]
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    candidates = ContextCandidateSet(
        candidates=(), total_chars=0, truncated=False
    )
    catalog = _exact_recipe_catalog()
    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "어제 유해란 LPGA 성적과 순위",
            candidates=candidates,
            catalog=catalog,
            router=router,
        )

    assert exc_info.value.boundary_code == "unknown_or_internal_tool"


@pytest.mark.asyncio
async def test_unknown_capability_primary_is_rejected() -> None:
    """catalog에 없는 capability-native primary는 fail-closed한다."""
    data = _response_data()
    data["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {
            "asset_type": "recipe",
            "asset_name": "invented-sports-live",
        },
        "supporting_assets": [],
        "fallback_modes": ["answer_with_evidence"],
        "reason": "invented exact asset",
    }
    data["execution"] = {
        "mode": "answer_with_evidence",
        "allowed_tools": [],
        "requires_confirmation": False,
        "complexity_signals": [],
        "reason": "fallback only",
    }
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "질문",
            candidates=_candidates(),
            catalog=_catalog(),
            router=router,
        )

    assert exc_info.value.boundary_code == "unknown_or_internal_asset"


@pytest.mark.asyncio
async def test_unknown_asset_type_name_pair_repairs_with_declared_catalog() -> None:
    """첫 invalid pair는 exact declared catalog 문맥으로 같은 route에서 복구한다."""
    invalid = _response_data()
    invalid["execution"]["primary_asset"] = {
        "asset_type": "native_tool",
        "asset_name": "realtime-lookup-skill",
    }
    invalid["execution"]["allowed_assets"] = [
        {
            "asset_type": "native_tool",
            "asset_name": "realtime-lookup-skill",
        }
    ]
    provider = AsyncMock()
    provider.send = AsyncMock(
        side_effect=[
            LLMResponse(text=json.dumps(invalid, ensure_ascii=False)),
            LLMResponse(text=_response_payload()),
        ]
    )
    router = LLMRouter(
        backends={},
        providers={"primary": provider},
        default_backend="primary",
        routes={"turn_analysis": LLMRoute("turn_analysis", "primary")},
    )

    plan = await plan_turn_with_llm(
        "오늘 있었던 발표야. 체크해봐",
        candidates=_candidates(),
        catalog=_catalog(),
        router=router,
    )

    assert plan.capability.primary_asset is not None
    assert plan.capability.primary_asset.asset_type == "skill"
    assert plan.capability.primary_asset.name == "realtime-lookup-skill"
    assert provider.send.await_count == 2
    retry_payload = json.loads(provider.send.await_args_list[1].args[1])
    repair = retry_payload["validation_repair"]
    assert repair["reason"] == "unknown_or_internal_asset"
    assert repair["allowed_asset_identities"] == [
        {"asset_type": "native_tool", "asset_name": "execute_skill"},
        {"asset_type": "skill", "asset_name": "realtime-lookup-skill"},
    ]
    assert repair["allowed_tool_names"] == ["execute_skill"]


@pytest.mark.asyncio
async def test_catalog_repair_rejects_repeated_unknown_asset() -> None:
    """repair 뒤에도 catalog 밖 identity를 반복하면 두 호출로 fail-closed한다."""
    invalid = _response_data()
    invalid["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {
            "asset_type": "skill",
            "asset_name": "internal-research-skill",
        },
        "supporting_assets": [],
        "fallback_modes": [],
        "reason": "invalid internal identity",
    }
    provider = AsyncMock()
    provider.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(invalid, ensure_ascii=False))
    )
    router = LLMRouter(
        backends={},
        providers={"primary": provider},
        default_backend="primary",
        routes={"turn_analysis": LLMRoute("turn_analysis", "primary")},
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "여러 출처로 조사해줘",
            candidates=_candidates(),
            catalog=_catalog(),
            router=router,
        )

    assert exc_info.value.boundary_code == "unknown_or_internal_asset"
    assert provider.send.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill_runtime_visible", "side_effecting_skill", "boundary_code"),
    [
        (False, False, "unknown_or_internal_asset"),
    ],
)
async def test_capability_primary_preserves_catalog_trust_boundaries(
    skill_runtime_visible: bool,
    side_effecting_skill: bool,
    boundary_code: str,
) -> None:
    """독립 primary도 internal asset 경계를 우회하지 못한다."""
    data = _response_data()
    data["capability"] = {
        "coverage": "full_coverage",
        "primary_asset": {
            "asset_type": "skill",
            "asset_name": "realtime-lookup-skill",
        },
        "supporting_assets": [],
        "fallback_modes": ["answer_with_evidence"],
        "reason": "exact asset owns the request",
    }
    data["execution"] = {
        "mode": "answer_with_evidence",
        "allowed_tools": [],
        "requires_confirmation": False,
        "complexity_signals": [],
        "reason": "fallback only",
    }
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "질문",
            candidates=_candidates(),
            catalog=_catalog(
                skill_runtime_visible=skill_runtime_visible,
                side_effecting_skill=side_effecting_skill,
            ),
            router=router,
        )

    assert exc_info.value.boundary_code == boundary_code


@pytest.mark.asyncio
async def test_unknown_allowed_tool_is_rejected() -> None:
    """runtime-visible native tool catalog에 없는 이름은 실행 scope에 들어오지 못한다."""
    data = _response_data()
    data["execution"]["allowed_tools"] = ["invented_tool"]
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "질문",
            candidates=_candidates(),
            catalog=_catalog(),
            router=router,
        )

    assert exc_info.value.boundary_code == "unknown_or_internal_tool"


@pytest.mark.asyncio
async def test_runtime_internal_allowed_tool_is_rejected() -> None:
    """catalog snapshot에만 있는 internal native tool도 합성 허용하지 않는다."""
    data = _response_data()
    data["execution"]["allowed_tools"] = ["internal_operator_tool"]
    base_catalog = _catalog()
    catalog = PlannerCatalog(
        assets=(
            *base_catalog.assets,
            PlannerAsset(
                asset_type="native_tool",
                name="internal_operator_tool",
                description="operator-only tool",
                domains=(),
                intents=(),
                read_only=True,
                side_effects=False,
                freshness_sensitive=False,
                direct_answer=False,
                requires_confirmation=False,
                output_contract=None,
                declared=True,
                runtime_visible=False,
            ),
        ),
        fingerprint=base_catalog.fingerprint,
    )
    router = AsyncMock()
    router.send = AsyncMock(
        return_value=LLMResponse(text=json.dumps(data, ensure_ascii=False))
    )

    with pytest.raises(PlannerUnavailable) as exc_info:
        await plan_turn_with_llm(
            "질문",
            candidates=_candidates(),
            catalog=catalog,
            router=router,
        )

    assert exc_info.value.boundary_code == "unknown_or_internal_tool"


@pytest.mark.asyncio
async def test_runtime_internal_asset_is_rejected() -> None:
    """catalog snapshot에 있어도 runtime_visible=false인 자산은 선택할 수 없다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_response_payload()))

    with pytest.raises(PlannerUnavailable):
        await plan_turn_with_llm(
            "질문",
            candidates=_candidates(),
            catalog=_catalog(skill_runtime_visible=False),
            router=router,
        )


@pytest.mark.asyncio
async def test_side_effecting_asset_requires_confirmation() -> None:
    """알려진 side-effect asset의 confirmation 누락은 안전한 방향으로 보정한다."""
    router = AsyncMock()
    router.send = AsyncMock(return_value=LLMResponse(text=_response_payload()))

    plan = await plan_turn_with_llm(
        "질문",
        candidates=_candidates(),
        catalog=_catalog(side_effecting_skill=True),
        router=router,
    )

    assert plan.execution.requires_confirmation is True
