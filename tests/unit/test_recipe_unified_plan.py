"""BIZ-496 — plan-selected recipe와 evidence owner 계약."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.evidence_policy import requirement_from_turn_plan
from simpleclaw.agent.orchestrator import AgentOrchestrator
from simpleclaw.agent.tool_loop import ToolLoopResult, ToolLoopRunner
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
from simpleclaw.recipes.models import RecipeDefinition


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
            mode=ExecutionMode.RECIPE,
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
async def test_recipe_owner_does_not_run_top_level_fact_controller(
    tmp_path,
    monkeypatch,
) -> None:
    orchestrator = AgentOrchestrator(_config(tmp_path))
    orchestrator._recipes = [
        RecipeDefinition(
            name="selected-recipe",
            description="selected",
            capability=CapabilityMetadata(
                read_only=True,
                side_effects=False,
                declared=True,
            ),
        ),
        RecipeDefinition(
            name="other-recipe",
            description="other",
            capability=CapabilityMetadata(
                read_only=True,
                side_effects=False,
                declared=True,
            ),
        ),
    ]

    async def fake_planner(_text, *, catalog, **_kwargs):
        return _plan(fingerprint=catalog.fingerprint)

    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    monkeypatch.setattr(orchestrator, "_reload_dynamic_files", lambda: None)
    planned_complex = AsyncMock(side_effect=AssertionError("duplicate fact retrieval"))
    monkeypatch.setattr(
        orchestrator,
        "_run_planned_complex_fact_workflow",
        planned_complex,
    )
    tool_loop = AsyncMock(return_value=ToolLoopResult("recipe result"))
    monkeypatch.setattr(orchestrator, "_run_tool_loop_result", tool_loop)

    result = await orchestrator.process_message("선택 레시피로 조사해줘", 1, 1)

    assert result == "recipe result"
    planned_complex.assert_not_awaited()
    tool_loop.assert_awaited_once()
    assert tool_loop.await_args.kwargs["plan"].fact_check.owner is EvidenceOwner.ASSET


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
