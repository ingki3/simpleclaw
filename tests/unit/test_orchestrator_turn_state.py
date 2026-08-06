"""BIZ-523 ordinary primary controller integration tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.orchestrator import (
    _UNIFIED_PLAN_REJECTED_MESSAGE,
    _UNIFIED_PLAN_UNAVAILABLE_MESSAGE,
    AgentOrchestrator,
)
from simpleclaw.agent.resolution_types import CapabilityCoverage, ComplexitySignal
from simpleclaw.agent.session_state import SessionIdentity
from simpleclaw.agent.tool_loop import ToolLoopResult
from simpleclaw.agent.turn_plan import (
    AssetRef,
    CapabilityPlan,
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
from simpleclaw.agent.turn_planner import PlannerUnavailable
from simpleclaw.capability import (
    CapabilityMetadata,
    parse_owned_binding_metadata,
    parse_owned_contract_metadata,
)
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.graph_runtime.status import DeliveryStatus
from simpleclaw.llm.models import LLMResponse
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.outbound_delivery import (
    PrimaryDeliveryCoordinator,
    PrimaryDeliveryMetadataV1,
    PrimaryResponseText,
)
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.models import SkillDefinition

REPO_ROOT = Path(__file__).parents[2]


@pytest.fixture
def config_file(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-test
      api_key: test-key
agent:
  db_path: "{tmp_path}/conversations.db"
  history_limit: 8
  max_tool_iterations: 2
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
  local_dir: "{tmp_path}/persona"
  global_dir: "{tmp_path}/global_persona"
  files: []
memory:
  rag:
    enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    (tmp_path / "persona").mkdir()
    (tmp_path / "global_persona").mkdir()
    return config


def _plan(
    text: str,
    fingerprint: str,
    *,
    mode: ExecutionMode = ExecutionMode.DIRECT_ANSWER,
    relation: ContextRelation = ContextRelation.STANDALONE,
    clarification: ClarificationPlan | None = None,
    fact: bool = False,
) -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text=text,
        context=ContextSelection(
            relation=relation,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question=text,
        ),
        clarification=clarification or ClarificationPlan(required=False),
        domains=("sports",) if fact else (),
        intents=("current_result",) if fact else (),
        fact_check=FactCheckPlan(
            required=fact,
            owner=EvidenceOwner.PLANNER if fact else EvidenceOwner.NONE,
            domain="sports" if fact else "",
            intents=("current_result",) if fact else (),
            entities=(
                (FactEntity(kind="league", value="LPGA"),)
                if fact
                else ()
            ),
            reference_date="2026-07-31" if fact else "",
            search_query=text if fact else "",
            required_claims=("official current result",) if fact else (),
            freshness_required=fact,
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=None,
            allowed_assets=(
                (AssetRef("native_tool", "web_search"),)
                if fact
                else ()
            ),
            allowed_tools=("web_search",) if fact else (),
            requires_confirmation=False,
            reason="test",
        ),
        confidence=0.95,
        decision_summary="test",
        catalog_fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_process_message_uses_one_primary_plan_and_scoped_store(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    calls = 0

    async def planner(text, *, catalog, **_kwargs):
        nonlocal calls
        calls += 1
        return _plan(text, catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    orchestrator._router.send = lambda _request: _async_response("typed answer")

    result = await orchestrator.process_message("hello", 10, 20)

    assert result == "typed answer"
    assert calls == 1
    session_key = SessionIdentity("telegram", "10", "20").stable_key()
    assert [
        message.content
        for message in orchestrator._store.get_recent(
            session_key=session_key
        )
    ] == ["hello", "typed answer"]


@pytest.mark.asyncio
async def test_v4_planner_failure_is_delivered_without_pre_send_assistant_row(
    config_file,
    monkeypatch,
    tmp_path,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    orchestrator._unified_turn_planner_config.update(
        {"architecture": "langgraph_v4", "mode": "primary"}
    )
    request_id = "telegram:20:planner-crash"
    session_key = SessionIdentity("telegram", "10", "20").stable_key()

    async def planner_crash(*_args, **_kwargs):
        raise RuntimeError("injected planner crash")

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner_crash,
    )

    response = await orchestrator.process_message(
        "must survive",
        10,
        20,
        request_id=request_id,
    )
    replay_response = await orchestrator.process_message(
        "must survive",
        10,
        20,
        request_id=request_id,
    )

    assert isinstance(response, PrimaryResponseText)
    assert isinstance(replay_response, PrimaryResponseText)
    assert str(response) == _UNIFIED_PLAN_UNAVAILABLE_MESSAGE
    assert replay_response.metadata == response.metadata
    assert [
        (message.role, message.content, message.turn_id)
        for message in orchestrator._store.get_recent(session_key=session_key)
    ] == [(MessageRole.USER, "must survive", request_id)]

    coordinator = PrimaryDeliveryCoordinator(
        journal_path=tmp_path / "planner-failure-delivery.db",
        conversation_store=orchestrator._store,
    )

    async def deliver(primary_response, destination_ref, sender):
        return await coordinator.deliver_telegram(
            primary_response,
            destination_ref=destination_ref,
            sender=sender,
        )

    bot = TelegramBot(
        "token",
        whitelist_user_ids=[10],
        primary_delivery_handler=deliver,
    )
    update = SimpleNamespace(
        message=SimpleNamespace(
            reply_text=AsyncMock(side_effect=RuntimeError("send failed"))
        )
    )

    outcome = await bot._send_response(
        update,
        response,
        chat_id=20,
        user_id=10,
    )
    replay_outcome = await bot._send_response(
        update,
        replay_response,
        chat_id=20,
        user_id=10,
    )

    assert outcome.delivery_receipt.status is DeliveryStatus.UNKNOWN
    assert replay_outcome.delivery_receipt == outcome.delivery_receipt
    assert outcome.persistence_receipt is None
    assert replay_outcome.persistence_receipt is None
    assert update.message.reply_text.await_count == 1
    assert [
        (message.role, message.content, message.turn_id)
        for message in orchestrator._store.get_recent(session_key=session_key)
    ] == [(MessageRole.USER, "must survive", request_id)]


@pytest.mark.asyncio
async def test_v4_connected_asset_failure_uses_delivery_boundary_without_assistant(
    config_file,
    monkeypatch,
    tmp_path,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    v4 = dict(orchestrator._unified_turn_planner_config.get("langgraph_v4", {}))
    v4.update(
        {
            "budget_valid": True,
            "on_failure": "fail_closed",
            "checkpoint": {"path": str(tmp_path / "asset-crash.db")},
        }
    )
    orchestrator._unified_turn_planner_config.update(
        {
            "architecture": "langgraph_v4",
            "mode": "primary",
            "langgraph_v4": v4,
        }
    )
    orchestrator._recipes = tuple(
        discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    )
    request_id = "telegram:20:asset-crash"
    session_key = SessionIdentity("telegram", "10", "20").stable_key()
    asset = AssetRef("recipe", "contract-fixture-workflow")

    async def planned_asset(text, *, catalog, **_kwargs):
        return replace(
            _plan(text, catalog.fingerprint),
            execution=ExecutionPlan(
                mode=ExecutionMode.DIRECT_ANSWER,
                primary_asset=asset,
                allowed_assets=(asset,),
                reason="asset crash fixture",
            ),
            capability=CapabilityPlan(
                coverage=CapabilityCoverage.FULL,
                primary_asset=asset,
                supporting_assets=(asset,),
                reason="asset crash fixture",
            ),
        )

    async def connected_asset_crash(**_kwargs):
        raise RuntimeError("injected connected asset crash")

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planned_asset,
    )
    monkeypatch.setattr(
        orchestrator,
        "_execute_langgraph_v4_connected",
        connected_asset_crash,
    )

    response = await orchestrator.process_message(
        "asset must fail closed",
        10,
        20,
        request_id=request_id,
    )
    replay_response = await orchestrator.process_message(
        "asset must fail closed",
        10,
        20,
        request_id=request_id,
    )

    assert isinstance(response, PrimaryResponseText)
    assert isinstance(replay_response, PrimaryResponseText)
    assert str(response) == _UNIFIED_PLAN_REJECTED_MESSAGE
    assert replay_response.metadata == response.metadata
    assert [
        (message.role, message.content, message.turn_id)
        for message in orchestrator._store.get_recent(session_key=session_key)
    ] == [(MessageRole.USER, "asset must fail closed", request_id)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_id", "request_text"),
    [
        ("telegram:20:stable-ordinary", "stable ordinary request"),
        (
            "telegram:callback:callback-id:20:1001:0",
            "stable callback option",
        ),
    ],
)
async def test_v4_telegram_replay_excludes_current_inbound_and_keeps_one_user_row(
    config_file,
    monkeypatch,
    request_id,
    request_text,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    orchestrator._unified_turn_planner_config.update(
        {"architecture": "langgraph_v4", "mode": "primary"}
    )
    session_key = SessionIdentity("telegram", "10", "20").stable_key()
    candidate_batches = []

    async def primary_result(_text, *, recent_rows, turn, **_kwargs):
        candidate_batches.append(list(recent_rows))
        return ToolLoopResult(
            "durable response",
            primary_delivery=PrimaryDeliveryMetadataV1(
                request_id=turn.turn_id,
                artifact_id="artifact-stable-ordinary",
                artifact_hash="hash-stable-ordinary",
                session_key=turn.session_key,
            ),
        )

    monkeypatch.setattr(
        orchestrator,
        "_run_unified_turn_planner_primary",
        primary_result,
    )

    first = await orchestrator.process_message(
        request_text,
        10,
        20,
        request_id=request_id,
    )
    replay = await orchestrator.process_message(
        request_text,
        10,
        20,
        request_id=request_id,
    )

    assert isinstance(first, PrimaryResponseText)
    assert isinstance(replay, PrimaryResponseText)
    assert candidate_batches == [[], []]
    assert [
        (message.role, message.content, message.turn_id)
        for message in orchestrator._store.get_recent(session_key=session_key)
    ] == [(MessageRole.USER, request_text, request_id)]


async def _async_response(text: str) -> LLMResponse:
    return LLMResponse(text=text, model="test")


@pytest.mark.asyncio
async def test_capability_first_exact_lpga_skips_generic_realtime_lookup(
    config_file,
    monkeypatch,
) -> None:
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            "agent:\n",
            "agent:\n"
            "  unified_turn_planner:\n"
            "    architecture: capability_first_v3\n"
            "    resolution_budget:\n"
            "      max_steps: 3\n"
            "      max_tool_calls: 3\n",
        ),
        encoding="utf-8",
    )
    orchestrator = AgentOrchestrator(config_file)
    skill = _supporting_skill("naver-sports-skill", direct_answer=True)
    skill = replace(
        skill,
        capability=replace(skill.capability, coverage="full_coverage"),
    )
    orchestrator._skills = [skill]
    orchestrator._skills_by_name = {skill.name: skill}
    orchestrator._reload_dynamic_files = lambda: None

    async def planner(text, *, catalog, **_kwargs):
        base = _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact=True,
        )
        return replace(
            base,
            fact_check=replace(
                base.fact_check,
                owner=EvidenceOwner.ASSET,
                search_query="",
                required_claims=("score",),
            ),
            capability=CapabilityPlan(
                coverage=CapabilityCoverage.FULL,
                primary_asset=AssetRef("skill", "naver-sports-skill"),
            ),
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    called: list[str] = []

    async def execute_skill(name: str, _args: str) -> str:
        called.append(name)
        return json.dumps(
            {
                "schema": "asset_result.v1",
                "status": "completed",
                "side_effect": False,
                "resolved_claims": ["score"],
                "evidence": [
                    {
                        "claim_id": "score",
                        "value": "70",
                        "source_url": "https://example.test/lpga",
                        "fresh": True,
                    }
                ],
                "data": {"text": "유해란 70타"},
            }
        )

    orchestrator._execute_skill = execute_skill
    result = await orchestrator.process_message("LPGA 유해란 스코어", 10, 20)
    assert result == "유해란 70타"
    assert called == ["naver-sports-skill"]


def _supporting_skill(
    name: str,
    *,
    direct_answer: bool = False,
) -> SkillDefinition:
    input_contract = parse_owned_contract_metadata(
        {
            "contract_id": f"skill.{name}.input",
            "version": "1",
            "owner_ref": {"type": "skill", "name": name},
            "json_schema": {
                "type": "object",
                "properties": {"args": {"type": "string"}},
                "required": ["args"],
                "additionalProperties": False,
            },
        },
        source=f"{name}.input",
    )
    output_contract = parse_owned_contract_metadata(
        {
            "contract_id": f"skill.{name}.output",
            "version": "1",
            "owner_ref": {"type": "skill", "name": name},
            "json_schema": {"type": "object"},
        },
        source=f"{name}.output",
    )
    binding = parse_owned_binding_metadata(
        {
            "binding_id": "shell-argv.v1",
            "owner_ref": {"type": "skill", "name": name},
            "binding": {"strategy": "shell", "order": ["args"]},
        },
        source=f"{name}.binding",
    )
    assert input_contract is not None
    assert output_contract is not None
    assert binding is not None
    return SkillDefinition(
        name=name,
        description=f"typed supporting asset {name}",
        capability=CapabilityMetadata(
            domains=("sports",),
            intents=("current_result",),
            read_only=True,
            side_effects=False,
            freshness_sensitive=True,
            direct_answer=direct_answer,
            output_contract="asset_result.v1",
            coverage="partial_coverage",
            input_contract="query.v1",
            declared=True,
        ),
        input_contract=input_contract,
        output_contract=output_contract,
        argument_binding=binding,
    )


def _enable_capability_v3(config_file, *, complex_enabled: bool = False) -> None:
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            "agent:\n",
            "agent:\n"
            "  unified_turn_planner:\n"
            "    architecture: capability_first_v3\n"
            "    resolution_budget:\n"
            "      max_steps: 4\n"
            "      max_tool_calls: 4\n"
            "      max_seconds: 1\n"
            "      max_tokens: 20\n"
            "    complex_escalation:\n"
            f"      enabled: {str(complex_enabled).lower()}\n",
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_capability_first_partial_path_runs_gap_controller_with_shared_budget(
    config_file,
    monkeypatch,
) -> None:
    _enable_capability_v3(config_file)
    orchestrator = AgentOrchestrator(config_file)
    skills = [_supporting_skill("score-lookup"), _supporting_skill("rank-lookup")]
    orchestrator._skills = skills
    orchestrator._skills_by_name = {skill.name: skill for skill in skills}
    orchestrator._reload_dynamic_files = lambda: None

    async def planner(text, *, catalog, **_kwargs):
        base = _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact=True,
        )
        return replace(
            base,
            fact_check=replace(
                base.fact_check,
                owner=EvidenceOwner.PLANNER,
                search_query=text,
                required_claims=("score", "rank"),
            ),
            capability=CapabilityPlan(
                coverage=CapabilityCoverage.PARTIAL,
                supporting_assets=(
                    AssetRef("skill", "score-lookup"),
                    AssetRef("skill", "rank-lookup"),
                ),
            ),
        )

    monkeypatch.setattr("simpleclaw.agent.orchestrator.plan_turn_with_llm", planner)
    called: list[str] = []

    async def execute_skill(name: str, _args: str) -> str:
        called.append(name)
        if name == "score-lookup":
            return json.dumps(
                {
                    "schema": "asset_result.v1",
                    "status": "partial_success",
                    "side_effect": False,
                    "resolved_claims": ["score"],
                    "unresolved_claims": ["rank"],
                    "next_questions": ["현재 순위를 확인한다"],
                    "usage": {"total_tokens": 4},
                    "evidence": [
                        {
                            "claim_id": "score",
                            "value": "70",
                            "source_url": "https://example.test/score",
                            "fresh": True,
                        }
                    ],
                }
            )
        return json.dumps(
            {
                "schema": "asset_result.v1",
                "status": "completed",
                "side_effect": False,
                "resolved_claims": ["rank"],
                "usage": {"total_tokens": 3},
                "evidence": [
                    {
                        "claim_id": "rank",
                        "value": "2",
                        "source_url": "https://example.test/rank",
                        "fresh": True,
                    }
                ],
                "data": {"text": "70타, 현재 2위"},
            }
        )

    orchestrator._execute_skill = execute_skill
    result = await orchestrator.process_message("현재 점수와 순위", 10, 20)

    assert result == "70타, 현재 2위"
    assert called == ["score-lookup", "rank-lookup"]


@pytest.mark.asyncio
async def test_capability_first_complex_path_runs_ordered_controller(
    config_file,
    monkeypatch,
) -> None:
    _enable_capability_v3(config_file, complex_enabled=True)
    orchestrator = AgentOrchestrator(config_file)
    skills = [_supporting_skill("input-lookup"), _supporting_skill("rule-lookup")]
    orchestrator._skills = skills
    orchestrator._skills_by_name = {skill.name: skill for skill in skills}
    orchestrator._reload_dynamic_files = lambda: None

    async def planner(text, *, catalog, **_kwargs):
        base = _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            fact=True,
        )
        return replace(
            base,
            fact_check=replace(
                base.fact_check,
                owner=EvidenceOwner.PLANNER,
                search_query=text,
                required_claims=("input", "rule"),
            ),
            execution=replace(
                base.execution,
                mode=ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
                complexity_signals=(
                    ComplexitySignal.ORDERED_CAPABILITY_COMPOSITION,
                ),
            ),
            capability=CapabilityPlan(
                coverage=CapabilityCoverage.PARTIAL,
                supporting_assets=(
                    AssetRef("skill", "input-lookup"),
                    AssetRef("skill", "rule-lookup"),
                ),
            ),
        )

    monkeypatch.setattr("simpleclaw.agent.orchestrator.plan_turn_with_llm", planner)
    called: list[str] = []

    async def execute_skill(name: str, _args: str) -> str:
        called.append(name)
        claim = "input" if name == "input-lookup" else "rule"
        return json.dumps(
            {
                "schema": "asset_result.v1",
                "status": "completed",
                "side_effect": False,
                "resolved_claims": [claim],
                "usage": {"total_tokens": 2},
                "evidence": [
                    {
                        "claim_id": claim,
                        "value": name,
                        "source_url": f"https://example.test/{claim}",
                        "fresh": True,
                    }
                ],
                "data": {"text": "순서대로 계산 완료"},
            }
        )

    orchestrator._execute_skill = execute_skill
    result = await orchestrator.process_message("입력과 규칙을 순서대로 계산", 10, 20)

    assert result == "순서대로 계산 완료"
    assert called == ["input-lookup", "rule-lookup"]


@pytest.mark.asyncio
async def test_capability_first_exact_path_enforces_in_flight_deadline(
    config_file,
    monkeypatch,
) -> None:
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            "agent:\n",
            "agent:\n"
            "  unified_turn_planner:\n"
            "    architecture: capability_first_v3\n"
            "    resolution_budget:\n"
            "      max_steps: 2\n"
            "      max_seconds: 0.01\n",
        ),
        encoding="utf-8",
    )
    orchestrator = AgentOrchestrator(config_file)
    skill = _supporting_skill("slow-lookup")
    skill = replace(
        skill,
        capability=replace(skill.capability, coverage="full_coverage"),
    )
    orchestrator._skills = [skill]
    orchestrator._skills_by_name = {skill.name: skill}
    orchestrator._reload_dynamic_files = lambda: None

    async def planner(text, *, catalog, **_kwargs):
        base = _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact=True,
        )
        return replace(
            base,
            fact_check=replace(
                base.fact_check,
                owner=EvidenceOwner.ASSET,
                search_query="",
                required_claims=("score",),
            ),
            capability=CapabilityPlan(
                coverage=CapabilityCoverage.FULL,
                primary_asset=AssetRef("skill", "slow-lookup"),
            ),
        )

    monkeypatch.setattr("simpleclaw.agent.orchestrator.plan_turn_with_llm", planner)

    async def slow_skill(*_args: object) -> str:
        await asyncio.sleep(0.05)
        return json.dumps(
            {
                "schema": "asset_result.v1",
                "status": "completed",
                "side_effect": False,
                "resolved_claims": ["score"],
            }
        )

    orchestrator._execute_skill = slow_skill
    result = await orchestrator.process_message("현재 점수", 10, 20)

    assert "확정 답변을 제한합니다" in result


@pytest.mark.asyncio
async def test_planner_unavailable_fails_closed_without_tool_execution(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)

    async def unavailable(*_args, **_kwargs):
        raise PlannerUnavailable("unavailable")

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        unavailable,
    )
    called = False

    async def dispatch(*_args, **_kwargs):
        nonlocal called
        called = True
        return "should not run"

    orchestrator._dispatch_tool_call = dispatch
    assert (
        await orchestrator.process_message("do something", 10, 20)
        == _UNIFIED_PLAN_UNAVAILABLE_MESSAGE
    )
    assert called is False


@pytest.mark.asyncio
async def test_unsupported_fact_lookup_never_enters_composer(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)

    async def planner(text, *, catalog, **_kwargs):
        return _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact=True,
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )

    async def unsupported(*_args, **_kwargs):
        return json.dumps(
            {
                "lookup_status": "unsupported",
                "evidence": [],
                "facts": [],
                "limitations": ["LPGA adapter unavailable"],
            }
        )

    orchestrator._execute_skill = unsupported
    composer_called = False

    async def composer(*_args, **_kwargs):
        nonlocal composer_called
        composer_called = True
        return LLMResponse(text="invented result", model="test")

    orchestrator._router.send = composer
    result = await orchestrator.process_message(
        "LPGA current result",
        10,
        20,
    )
    assert "확정할 수 없습니다" in result
    assert composer_called is False


@pytest.mark.asyncio
async def test_found_low_confidence_fact_returns_limited_final(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)

    async def planner(text, *, catalog, **_kwargs):
        return _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.ANSWER_WITH_EVIDENCE,
            fact=True,
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )

    async def low_confidence_found(*_args, **_kwargs):
        return json.dumps(
            {
                "lookup_status": "found",
                "kind": "sports",
                "confidence": "low",
                "facts": [
                    {
                        "claim": "LPGA final score 68 and win",
                        "source_url": "https://example.test/lpga",
                    }
                ],
                "timeline_validation": {"status": "final"},
                "limitations": ["low confidence"],
            }
        )

    orchestrator._execute_skill = low_confidence_found
    composer_called = False

    async def composer(*_args, **_kwargs):
        nonlocal composer_called
        composer_called = True
        return LLMResponse(text="invented 68 and win", model="test")

    orchestrator._router.send = composer

    result = await orchestrator.process_message("LPGA current result", 10, 20)

    assert "확정할 수 없습니다" in result
    assert "LPGA final score 68 and win" not in result
    assert "https://example.test/lpga" not in result
    assert composer_called is False


@pytest.mark.asyncio
async def test_context_candidates_do_not_cross_session(
    config_file,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(config_file)
    session_a = SessionIdentity("telegram", "10", "20").stable_key()
    session_b = SessionIdentity("telegram", "11", "20").stable_key()
    orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.USER, content="A context"),
        session_key=session_a,
    )
    orchestrator._store.add_message(
        ConversationMessage(role=MessageRole.USER, content="B secret"),
        session_key=session_b,
    )

    async def planner(text, *, candidates, catalog, **_kwargs):
        contents = [item.content for item in candidates.candidates]
        assert "A context" in contents
        assert "B secret" not in contents
        return _plan(text, catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    orchestrator._router.send = lambda _request: _async_response("ok")
    assert await orchestrator.process_message("next", 10, 20) == "ok"


@pytest.mark.asyncio
async def test_clarification_survives_orchestrator_reopen(
    config_file,
    monkeypatch,
) -> None:
    first = AgentOrchestrator(config_file)

    async def planner(text, *, catalog, **_kwargs):
        return _plan(
            text,
            catalog.fingerprint,
            mode=ExecutionMode.CLARIFY,
            relation=ContextRelation.UNCLEAR,
            clarification=ClarificationPlan(
                required=True,
                question="Which league?",
                options=("KBO", "LPGA"),
            ),
        )

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    assert "Which league?" in await first.process_message("that result", 10, 20)

    reopened = AgentOrchestrator(config_file)
    pending = reopened.get_pending_clarify(10, 20)
    assert pending is not None
    assert [option.body for option in pending.options] == ["KBO", "LPGA"]
