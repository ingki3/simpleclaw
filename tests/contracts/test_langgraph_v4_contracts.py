"""V4 primary fail-closed receipt와 promotion-zero 계약."""

from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4ExecutionReceiptV1,
    ShadowBudgetUsageV1,
    ShadowSideEffectCountsV1,
    TargetDispatchTraceV1,
)
from simpleclaw.graph_runtime.shadow import ConnectedExecutionError
from simpleclaw.graph_runtime.status import TerminalOutcome


def test_primary_pre_dispatch_failure_has_typed_reason_and_zero_promotion() -> None:
    receipt = LangGraphV4ExecutionReceiptV1(
        mode="primary",
        request_id="pre-dispatch-failure",
        selected_route="recipe",
        final_artifact=None,
        dispatch_trace=TargetDispatchTraceV1(
            target_asset_ref=AssetRefV1(type="recipe", name="sports-live"),
            invocation_id="incident-invocation",
            attempted=0,
            executed=0,
            succeeded=0,
        ),
        budget_usage=ShadowBudgetUsageV1(
            max_graph_steps=40,
            max_asset_calls=12,
            max_llm_calls=8,
            max_tokens=16000,
            max_seconds=180,
            max_parallel_invocations=3,
            graph_steps=0,
            asset_calls=0,
            llm_calls=1,
            tokens=100,
            elapsed_seconds=0,
            parallel_peak=0,
            stop_condition="blocked",
        ),
        side_effect_counts=ShadowSideEffectCountsV1(),
        terminal_outcome=TerminalOutcome.BLOCKED,
        rollback_required=True,
        rollback_reasons=("registry:definition.contract_metadata_incomplete",),
    )

    assert receipt.rollback_required is True
    assert receipt.dispatch_trace.attempted == 0
    assert receipt.final_content is None
    assert receipt.side_effect_counts.total == 0


def test_connected_error_provenance_is_closed_and_hash_only() -> None:
    marker = "ASCII_ASSET_IDENTITY_MARKER_614"
    diagnostic = ConnectedExecutionError(
        "binding",
        ValueError(marker),
        code=marker,
        selected_asset_kind=marker,
        selected_asset_hash=marker,
        approved_asset_hash=marker,
        catalog_fingerprint=marker,
        registry_fingerprint=marker,
    )

    assert diagnostic.phase == "binding"
    assert diagnostic.code == "connected_binding_failed"
    assert diagnostic.error_type == "ValueError"
    assert diagnostic.selected_asset_kind == "unknown"
    assert diagnostic.selected_asset_hash == ""
    assert diagnostic.approved_asset_hash == ""
    assert diagnostic.catalog_fingerprint == ""
    assert diagnostic.registry_fingerprint == ""
    assert marker not in str(diagnostic)
