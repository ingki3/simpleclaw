"""BIZ-497 — Unified TurnPlanner deterministic canary와 rollback 계약."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.orchestrator import (
    AgentOrchestrator,
    _UNIFIED_PLAN_UNAVAILABLE_MESSAGE,
    _canary_read_only_eligible,
    _deterministic_rollout_sample,
)
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.tool_loop import ToolLoopResult
from simpleclaw.agent.turn_analysis import TurnAnalysis
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
from simpleclaw.agent.turn_planner import PlannerUnavailable
from simpleclaw.logging.structured_logger import StructuredLogger


def _config(tmp_path, *, mode: str, sample_rate: float):
    """테스트별 rollout mode를 가진 최소 런타임 설정을 만든다."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""\
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-test
      api_key: test-key
agent:
  history_limit: 8
  db_path: "{tmp_path}/conversations.db"
  unified_turn_planner:
    mode: {mode}
    sample_rate: {sample_rate}
  turn_analysis:
    enabled: true
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
    (tmp_path / "local_skills").mkdir()
    (tmp_path / "global_skills").mkdir()
    persona = tmp_path / "persona_local"
    persona.mkdir()
    (persona / "AGENT.md").write_text("# Agent", encoding="utf-8")
    return config


def test_deterministic_canary_sampling_is_stable_and_bounded():
    """같은 cohort는 재시작과 무관하게 같은 결정을 받고 경계값은 고정된다."""
    decisions = {
        _deterministic_rollout_sample(
            user_id=user_id,
            chat_id=user_id * 10,
            sample_rate=0.25,
        )
        for user_id in range(1, 50)
    }

    assert decisions == {False, True}
    assert _deterministic_rollout_sample(
        user_id=17,
        chat_id=170,
        sample_rate=0.25,
    ) == _deterministic_rollout_sample(
        user_id=17,
        chat_id=170,
        sample_rate=0.25,
    )
    assert not _deterministic_rollout_sample(
        user_id=17,
        chat_id=170,
        sample_rate=0.0,
    )
    assert _deterministic_rollout_sample(
        user_id=17,
        chat_id=170,
        sample_rate=1.0,
    )


def _plan(
    *,
    mode: ExecutionMode,
    asset: AssetRef | None = None,
    fact_required: bool = False,
) -> UnifiedTurnPlan:
    """canary eligibility 경계를 검증할 최소 불변 plan을 만든다."""
    allowed_assets = (asset,) if asset is not None else ()
    return UnifiedTurnPlan(
        original_text="원문",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="독립 질문",
        ),
        clarification=ClarificationPlan(required=False),
        domains=(),
        intents=(),
        fact_check=FactCheckPlan(
            required=fact_required,
            owner=EvidenceOwner.PLANNER if fact_required else EvidenceOwner.NONE,
            domain="test" if fact_required else "none",
            entities=(),
            search_query="query" if fact_required else "",
        ),
        execution=ExecutionPlan(
            mode=mode,
            primary_asset=asset,
            allowed_assets=allowed_assets,
            allowed_tools=("execute_skill",) if asset is not None else (),
            requires_confirmation=False,
            reason="test",
        ),
        confidence=0.9,
        decision_summary="test",
    )


def _catalog_asset(
    *,
    read_only: bool,
    side_effects: bool,
    requires_confirmation: bool = False,
    asset_type: str = "skill",
    name: str = "lookup",
) -> PlannerCatalog:
    """선언된 skill 하나의 capability catalog를 만든다."""
    return PlannerCatalog(
        assets=(
            PlannerAsset(
                asset_type=asset_type,
                name=name,
                description="declared lookup",
                domains=(),
                intents=(),
                read_only=read_only,
                side_effects=side_effects,
                freshness_sensitive=False,
                direct_answer=True,
                requires_confirmation=requires_confirmation,
                output_contract=None,
                declared=True,
                runtime_visible=True,
            ),
        ),
        fingerprint="catalog-v1",
    )


def test_canary_eligibility_allows_only_direct_or_declared_read_only_asset():
    """Phase 2는 fact/complex와 mutation asset을 legacy로 제한한다."""
    asset = AssetRef("skill", "lookup")

    assert _canary_read_only_eligible(
        _plan(mode=ExecutionMode.DIRECT_ANSWER),
        PlannerCatalog(assets=(), fingerprint="empty"),
    )
    assert _canary_read_only_eligible(
        _plan(mode=ExecutionMode.EXECUTE_ASSET, asset=asset),
        _catalog_asset(read_only=True, side_effects=False),
    )
    native_plan = _plan(
        mode=ExecutionMode.EXECUTE_ASSET,
        asset=AssetRef("native_tool", "web_search"),
    )
    native_plan = replace(
        native_plan,
        execution=replace(
            native_plan.execution,
            allowed_tools=("web_search",),
        ),
    )
    assert _canary_read_only_eligible(
        native_plan,
        _catalog_asset(
            read_only=True,
            side_effects=False,
            asset_type="native_tool",
            name="web_search",
        ),
    )
    assert not _canary_read_only_eligible(
        _plan(mode=ExecutionMode.EXECUTE_ASSET, asset=asset),
        _catalog_asset(read_only=False, side_effects=True),
    )
    assert not _canary_read_only_eligible(
        _plan(mode=ExecutionMode.FACT_CHECK, fact_required=True),
        PlannerCatalog(assets=(), fingerprint="empty"),
    )


@pytest.mark.asyncio
async def test_sampled_canary_uses_unified_primary_and_skips_legacy(
    tmp_path,
    monkeypatch,
):
    """sampled cohort는 Unified primary 결과를 사용하고 TurnAnalysis를 부르지 않는다."""
    orchestrator = AgentOrchestrator(
        _config(tmp_path, mode="canary", sample_rate=1.0)
    )
    primary = AsyncMock(return_value=ToolLoopResult("canary 답변"))
    analyzer = AsyncMock(side_effect=AssertionError("legacy analyzer called"))
    monkeypatch.setattr(
        orchestrator,
        "_run_unified_turn_planner_primary",
        primary,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        analyzer,
    )

    result = await orchestrator.process_message(
        "질문",
        user_id=11,
        chat_id=22,
    )

    assert result == "canary 답변"
    primary.assert_awaited_once()
    assert primary.call_args.kwargs["canary_read_only"] is True
    analyzer.assert_not_awaited()


@pytest.mark.asyncio
async def test_sampled_out_canary_and_off_mode_use_legacy_path(
    tmp_path,
    monkeypatch,
):
    """sampled-out canary와 rollback off는 Planner를 호출하지 않는다."""
    for mode in ("canary", "off"):
        mode_dir = tmp_path / mode
        mode_dir.mkdir()
        orchestrator = AgentOrchestrator(
            _config(mode_dir, mode=mode, sample_rate=0.0)
        )
        primary = AsyncMock(side_effect=AssertionError("planner called"))
        analyzer = AsyncMock(
            return_value=TurnAnalysis(
                original_text="질문",
                normalized_question="legacy 질문",
            )
        )
        monkeypatch.setattr(
            orchestrator,
            "_run_unified_turn_planner_primary",
            primary,
        )
        monkeypatch.setattr(
            "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
            analyzer,
        )
        orchestrator._tool_loop = AsyncMock(return_value="legacy 답변")

        result = await orchestrator.process_message(
            "질문",
            user_id=11,
            chat_id=22,
        )

        assert result == "legacy 답변"
        primary.assert_not_awaited()
        analyzer.assert_awaited_once()


@pytest.mark.asyncio
async def test_sampled_canary_planner_unavailable_fails_closed(
    tmp_path,
    monkeypatch,
):
    """sampled cohort의 PlannerUnavailable은 legacy semantic path로 내려가지 않는다."""
    orchestrator = AgentOrchestrator(
        _config(tmp_path, mode="canary", sample_rate=1.0)
    )
    planner = AsyncMock(side_effect=PlannerUnavailable("private provider body"))
    analyzer = AsyncMock(side_effect=AssertionError("legacy analyzer called"))
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        planner,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        analyzer,
    )

    result = await orchestrator.process_message(
        "PRIVATE_CURRENT",
        user_id=11,
        chat_id=22,
    )

    assert result == _UNIFIED_PLAN_UNAVAILABLE_MESSAGE
    analyzer.assert_not_awaited()
    assert "PRIVATE" not in result


@pytest.mark.asyncio
async def test_canary_fact_plan_returns_to_legacy_without_primary_execution(
    tmp_path,
    monkeypatch,
):
    """Phase 2 sampled fact plan은 실행하지 않고 검증된 legacy 경로로 제한한다."""
    orchestrator = AgentOrchestrator(
        _config(tmp_path, mode="canary", sample_rate=1.0)
    )

    async def fake_planner(_text, *, catalog, **_kwargs):
        fact_plan = _plan(
            mode=ExecutionMode.FACT_CHECK,
            fact_required=True,
        )
        return replace(
            fact_plan,
            execution=replace(
                fact_plan.execution,
                allowed_tools=("web_search",),
            ),
            catalog_fingerprint=catalog.fingerprint,
        )

    analyzer = AsyncMock(
        return_value=TurnAnalysis(
            original_text="현재 사실",
            normalized_question="legacy 현재 사실",
        )
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        analyzer,
    )
    orchestrator._tool_loop = AsyncMock(return_value="legacy 검증 답변")

    result = await orchestrator.process_message(
        "현재 사실",
        user_id=11,
        chat_id=22,
    )

    assert result == "legacy 검증 답변"
    analyzer.assert_awaited_once()
    orchestrator._tool_loop.assert_awaited_once()


@pytest.mark.asyncio
async def test_canary_mutation_plan_fails_closed_without_legacy_or_execution(
    tmp_path,
    monkeypatch,
):
    """mutation plan은 legacy로 우회하거나 직접 실행하지 않고 확인 gate에서 멈춘다."""
    orchestrator = AgentOrchestrator(
        _config(tmp_path, mode="canary", sample_rate=1.0)
    )

    async def fake_planner(_text, *, catalog, **_kwargs):
        mutation = next(
            asset
            for asset in catalog.assets
            if asset.runtime_visible and asset.side_effects
        )
        ref = AssetRef(mutation.asset_type, mutation.name)
        plan = _plan(mode=ExecutionMode.EXECUTE_ASSET, asset=ref)
        return replace(
            plan,
            execution=replace(
                plan.execution,
                allowed_tools=(
                    (mutation.name,)
                    if mutation.asset_type == "native_tool"
                    else ("execute_skill",)
                ),
                requires_confirmation=True,
            ),
            catalog_fingerprint=catalog.fingerprint,
        )

    analyzer = AsyncMock(side_effect=AssertionError("legacy analyzer called"))
    tool_loop = AsyncMock(side_effect=AssertionError("mutation executed"))
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.plan_turn_with_llm",
        fake_planner,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        analyzer,
    )
    orchestrator._tool_loop = tool_loop

    result = await orchestrator.process_message(
        "파일을 바꿔줘",
        user_id=11,
        chat_id=22,
    )

    assert "확인" in result
    analyzer.assert_not_awaited()
    tool_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_canary_legacy_metric_contains_no_user_text(
    tmp_path,
    monkeypatch,
):
    """sampled-out fallback 계측은 경로·사유만 남기고 원문을 기록하지 않는다."""
    structured_logger = StructuredLogger(tmp_path / "logs")
    orchestrator = AgentOrchestrator(
        _config(tmp_path, mode="canary", sample_rate=0.0),
        structured_logger=structured_logger,
    )
    monkeypatch.setattr(
        "simpleclaw.agent.orchestrator.analyze_turn_with_llm",
        AsyncMock(
            return_value=TurnAnalysis(
                original_text="PRIVATE_CURRENT",
                normalized_question="legacy 질문",
            )
        ),
    )
    orchestrator._tool_loop = AsyncMock(return_value="legacy 답변")

    await orchestrator.process_message(
        "PRIVATE_CURRENT",
        user_id=11,
        chat_id=22,
    )

    entries = structured_logger.get_entries()
    rollout = [
        entry
        for entry in entries
        if entry.action_type == "unified_turn_planner_rollout"
    ]
    assert len(rollout) == 1
    assert rollout[0].trace_id == ""
    assert rollout[0].details["selected_path"] == "legacy"
    assert rollout[0].details["reason"] == "canary_sampled_out"
    assert "PRIVATE" not in rollout[0].to_json()
