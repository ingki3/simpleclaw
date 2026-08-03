"""BIZ-496 — plan-selected recipe와 evidence owner 계약."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.capability_executor import ASSET_RESULT_RESPONSE_SCHEMA
from simpleclaw.agent.evidence_policy import requirement_from_turn_plan
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_loop import ToolLoopResult, ToolLoopRunner, ToolTraceStep
from simpleclaw.agent.turn_plan import (
    AssetRef,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionMode,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.capability import CapabilityMetadata
from simpleclaw.llm.models import LLMResponse
from simpleclaw.recipes.loader import load_recipe
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition

SPORTS_RECIPE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "recipes"
    / "sports-live"
    / "recipe.yaml"
)


def _delegate_trace(
    skill_name: str = "naver-sports-skill",
    *,
    success: bool = True,
) -> list[ToolTraceStep]:
    return [
        ToolTraceStep(
            tool_name="execute_skill",
            arguments={"skill_name": skill_name},
            observation_preview='{"ok": true}',
            success=success,
        )
    ]


def _config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-2.0-flash
      api_key: test-key
agent:
  db_path: "{tmp_path}/conversations.db"
  unified_turn_planner:
    mode: primary
recipes:
  dir: "{tmp_path}/recipes"
skills:
  local_dir: "{tmp_path}/local_skills"
  global_dir: "{tmp_path}/global_skills"
persona:
  local_dir: "{tmp_path}/persona_local"
  global_dir: "{tmp_path}/persona_global"
  files:
    - name: AGENT.md
      type: agent
memory:
  rag:
    enabled: false
""",
        encoding="utf-8",
    )
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent", encoding="utf-8")
    return config


def _plan(
    *,
    fingerprint: str = "",
    owner: EvidenceOwner = EvidenceOwner.ASSET,
) -> UnifiedTurnPlan:
    selected = AssetRef("recipe", "selected-recipe")
    return UnifiedTurnPlan(
        original_text="선택 레시피로 조사해줘",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="선택 레시피로 조사해줘",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("research",),
        intents=("report",),
        fact_check=FactCheckPlan(
            required=owner is EvidenceOwner.ASSET,
            owner=owner,
            domain="research",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(
            mode=ExecutionMode.DIRECT_ANSWER,
            primary_asset=selected,
            allowed_assets=(
                selected,
                AssetRef("recipe", "other-recipe"),
            ),
            allowed_tools=(),
            requires_confirmation=False,
            reason="selected recipe owns execution",
        ),
        confidence=0.95,
        decision_summary="recipe",
        catalog_fingerprint=fingerprint,
    )


@pytest.mark.asyncio
async def test_recipe_mode_exposes_only_selected_recipe(tmp_path) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._recipes = [
        RecipeDefinition(
            name="selected-recipe",
            description="SELECTED_RECIPE_DESCRIPTION",
            capability=CapabilityMetadata(
                read_only=True,
                side_effects=False,
                declared=True,
            ),
        ),
        RecipeDefinition(
            name="other-recipe",
            description="OTHER_RECIPE_DESCRIPTION",
            capability=CapabilityMetadata(
                read_only=True,
                side_effects=False,
                declared=True,
            ),
        ),
    ]

    state = await orchestrator._prepare_tool_loop_state(
        "원문",
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=_plan(),
        candidates=ContextCandidateSet(
            candidates=(),
            total_chars=0,
            truncated=False,
        ),
    )

    assert "SELECTED_RECIPE_DESCRIPTION" in state.system_prompt
    assert "OTHER_RECIPE_DESCRIPTION" not in state.system_prompt
    assert state.execution_scope is not None
    assert state.execution_scope.allowed_assets == frozenset(
        {("recipe", "selected-recipe")}
    )


@pytest.mark.asyncio
async def test_exact_recipe_nested_scope_exposes_only_delegate_skill(tmp_path) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._skills = [
        SkillDefinition(
            name="naver-sports-skill",
            description="ALLOWED_SPORTS_DELEGATE",
        ),
        SkillDefinition(
            name="realtime-lookup-skill",
            description="FORBIDDEN_GENERIC_DELEGATE",
        ),
    ]

    state = await orchestrator._prepare_tool_loop_state(
        "typed recipe instructions",
        True,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        forced_skill_names=frozenset({"naver-sports-skill"}),
        forced_tool_names=frozenset({"execute_skill"}),
    )

    assert "ALLOWED_SPORTS_DELEGATE" in state.system_prompt
    assert "FORBIDDEN_GENERIC_DELEGATE" not in state.system_prompt
    assert [tool.name for tool in state.tools] == ["execute_skill"]
    assert state.execution_scope is not None
    assert state.execution_scope.allowed_tools == frozenset({"execute_skill"})
    assert state.execution_scope.allowed_assets == frozenset(
        {("skill", "naver-sports-skill")}
    )
    assert state.execution_scope.operator_tools is False
    assert state.execution_scope.allow_cron_mutation is False
    assert state.execution_scope.max_tool_calls == 1


@pytest.mark.asyncio
async def test_exact_instructions_recipe_returns_one_typed_envelope(tmp_path) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._recipes = [load_recipe(SPORTS_RECIPE)]
    nested = AsyncMock(
        return_value=ToolLoopResult(
            text=json.dumps(
                {
                    "schema": "asset_result.v1",
                    "status": "completed",
                    "resolved_claims": ["score"],
                    "evidence": [
                        {
                            "claim_id": "score",
                            "source_url": "https://sports.example/result",
                            "fresh": True,
                        }
                    ],
                }
            ),
            trace=_delegate_trace(),
            success=True,
        )
    )
    orchestrator._run_tool_loop_result = nested

    result = await orchestrator._execute_exact_recipe_asset(
        "sports-live",
        {"query": "어제 유해란 LPGA 성적과 순위"},
    )

    assert isinstance(result, dict)
    assert result["schema"] == "asset_result.v1"
    assert result["status"] == "completed"
    kwargs = nested.await_args.kwargs
    assert kwargs["isolated"] is True
    assert kwargs["on_text_delta"] is None
    assert kwargs["operator_tools"] is False
    assert kwargs["allow_cron_mutation"] is False
    assert kwargs["forced_skill_names"] == frozenset({"naver-sports-skill"})
    assert kwargs["forced_tool_names"] == frozenset({"execute_skill"})
    assert kwargs["final_response_schema"] is ASSET_RESULT_RESPONSE_SCHEMA


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trace", "success", "limitation"),
    [
        ([], True, "recipe_requires_one_successful_delegate"),
        (
            _delegate_trace(success=False),
            True,
            "recipe_requires_one_successful_delegate",
        ),
        (
            _delegate_trace("realtime-lookup-skill"),
            True,
            "recipe_requires_one_successful_delegate",
        ),
        (_delegate_trace(), False, "typed_recipe_nested_loop_failed"),
    ],
    ids=(
        "zero-call",
        "failed-delegate",
        "wrong-delegate",
        "multiple-call-capped",
    ),
)
async def test_exact_instructions_recipe_requires_one_successful_delegate(
    tmp_path,
    trace: list[ToolTraceStep],
    success: bool,
    limitation: str,
) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._recipes = [load_recipe(SPORTS_RECIPE)]
    orchestrator._run_tool_loop_result = AsyncMock(
        return_value=ToolLoopResult(
            text=json.dumps(
                {
                    "schema": "asset_result.v1",
                    "status": "completed",
                    "resolved_claims": ["score"],
                }
            ),
            trace=trace,
            success=success,
        )
    )

    result = await orchestrator._execute_exact_recipe_asset(
        "sports-live",
        {"query": "현재 LPGA 결과"},
    )

    assert result == {
        "schema": "asset_result.v1",
        "status": "failed_terminal",
        "limitations": [limitation],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nested_text",
    [
        "plain answer",
        '```json\n{"schema":"asset_result.v1","status":"completed"}\n```',
        '{"schema":"asset_result.v1","status":"completed"}\n'
        '{"schema":"asset_result.v1","status":"completed"}',
    ],
    ids=("plain", "fenced", "multiple-objects"),
)
async def test_exact_instructions_recipe_rejects_untyped_nested_output(
    tmp_path,
    nested_text: str,
) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._recipes = [load_recipe(SPORTS_RECIPE)]
    orchestrator._run_tool_loop_result = AsyncMock(
        return_value=ToolLoopResult(
            text=nested_text,
            trace=_delegate_trace(),
            success=True,
        )
    )

    result = await orchestrator._execute_exact_recipe_asset(
        "sports-live",
        {"query": "현재 LPGA 결과"},
    )

    assert result == {
        "schema": "asset_result.v1",
        "status": "failed_terminal",
        "limitations": ["recipe_requires_one_typed_envelope"],
    }




@pytest.mark.asyncio
async def test_asset_owned_recipe_runs_through_common_evidence_gate(tmp_path) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._recipes = [
        RecipeDefinition(
            name="selected-recipe",
            description="selected evidence recipe",
            capability=CapabilityMetadata(
                read_only=True,
                side_effects=False,
                declared=True,
            ),
        ),
    ]
    plan = _plan()
    state = await orchestrator._prepare_tool_loop_state(
        plan.context.standalone_question,
        False,
        attachments=None,
        on_text_delta=None,
        on_progress=None,
        plan=plan,
        candidates=ContextCandidateSet(
            candidates=(),
            total_chars=0,
            truncated=False,
        ),
        evidence_requirement=requirement_from_turn_plan(plan),
    )
    orchestrator._router.send = AsyncMock(
        return_value=LLMResponse(
            text=(
                "선택 레시피로 조사해줘 결과\n"
                "URL: https://example.com/recipe-evidence"
            ),
            model="test",
        )
    )

    result = await ToolLoopRunner(orchestrator).run(state)

    assert result.success is True
    assert "recipe-evidence" in result.text
    assert state.evidence_state is not None
    assert state.evidence_state.usable is True
    orchestrator._router.send.assert_awaited_once()
