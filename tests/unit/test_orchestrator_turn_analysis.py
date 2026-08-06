"""V4 primary original/effective plan diagnostic 회귀."""

from dataclasses import replace

from simpleclaw.agent.orchestrator import _selected_asset_identity
from simpleclaw.agent.resolution_types import CapabilityCoverage, ExecutionMode
from simpleclaw.agent.turn_plan import (
    AssetRef,
    CapabilityPlan,
    ClarificationPlan,
    ContextRelation,
    ContextSelection,
    EvidenceOwner,
    ExecutionPlan,
    FactCheckPlan,
    UnifiedTurnPlan,
)
from simpleclaw.graph_runtime.shadow import ConnectedExecutionError


def _asset_zero_plan() -> UnifiedTurnPlan:
    return UnifiedTurnPlan(
        original_text="raw prompt must not enter diagnostics",
        context=ContextSelection(
            relation=ContextRelation.STANDALONE,
            use_prior_context=False,
            selected_turn_ids=(),
            standalone_question="raw prompt must not enter diagnostics",
        ),
        clarification=ClarificationPlan(required=False),
        domains=("sports",),
        intents=("standings",),
        fact_check=FactCheckPlan(
            required=True,
            owner=EvidenceOwner.PLANNER,
            domain="sports",
            entities=(),
            search_query="",
        ),
        execution=ExecutionPlan(mode=ExecutionMode.DIRECT_ANSWER),
        capability=CapabilityPlan(coverage=CapabilityCoverage.NO_MATCH),
        confidence=1.0,
        decision_summary="fixture",
    )


def test_original_and_effective_asset_diagnostic_are_distinct() -> None:
    original = _asset_zero_plan()
    effective = replace(
        original,
        capability=replace(
            original.capability,
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", "sports-live"),
        ),
    )

    assert _selected_asset_identity(original) == "none"
    assert _selected_asset_identity(effective) == "recipe:sports-live"


def test_connected_exception_message_redacts_raw_non_ascii_and_credentials() -> None:
    diagnostic = ConnectedExecutionError(
        "registry",
        ValueError("KBO 원문 token=do-not-log"),
    )

    assert diagnostic.phase == "registry"
    assert diagnostic.error_type == "ValueError"
    assert "KBO" not in diagnostic.safe_message
    assert "do-not-log" not in diagnostic.safe_message
    assert diagnostic.safe_message.startswith("redacted_non_ascii_message_sha256=")
