"""V4 primary fail-closed receipt와 promotion-zero 계약."""

from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4ExecutionReceiptV1,
    ShadowBudgetUsageV1,
    ShadowSideEffectCountsV1,
    TargetDispatchTraceV1,
)
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
