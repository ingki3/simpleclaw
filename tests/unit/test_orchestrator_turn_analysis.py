"""V4 primary original/effective plan diagnostic 회귀."""

from dataclasses import replace

import pytest

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


@pytest.mark.parametrize(
    "raw_message",
    [
        "prompt=ASCII_PRIVATE_PROMPT_MARKER_639",
        '{"api_key":"json-private-key","password":"json-password"}',
        "authorization=Bearer provider-private-token",
        "https://provider.example/v1?token=url-private-token",
        "사용자 비공개 질문",
    ],
)
def test_connected_exception_message_is_digest_only(raw_message: str) -> None:
    diagnostic = ConnectedExecutionError(
        "registry_lookup",
        ValueError(raw_message),
        code="asset_not_registered_read_only",
        selected_asset_identity="recipe:sports-live",
        selected_asset_hash="asset-hash",
        catalog_fingerprint="catalog-hash",
        registry_fingerprint="registry-hash",
        owned_input_contract_present=False,
        owned_output_contract_present=False,
        owned_binding_present=False,
    )

    assert diagnostic.phase == "registry_lookup"
    assert diagnostic.code == "asset_not_registered_read_only"
    assert diagnostic.error_type == "ValueError"
    assert diagnostic.selected_asset_identity == "recipe:sports-live"
    assert diagnostic.selected_asset_hash == "asset-hash"
    assert diagnostic.catalog_fingerprint == "catalog-hash"
    assert diagnostic.registry_fingerprint == "registry-hash"
    assert diagnostic.owned_input_contract_present is False
    assert diagnostic.owned_output_contract_present is False
    assert diagnostic.owned_binding_present is False
    assert raw_message not in diagnostic.safe_message
    assert diagnostic.safe_message.startswith("message_sha256=")
    assert len(diagnostic.safe_message) == len("message_sha256=") + 16


def test_connected_exception_does_not_promote_provider_code() -> None:
    class ProviderError(Exception):
        code = "ASCII_PRIVATE_PROMPT_MARKER_639"

    cause = ProviderError("private provider payload")

    diagnostic = ConnectedExecutionError(
        "ASCII_PRIVATE_PROMPT_MARKER_639",  # type: ignore[arg-type]
        cause,
    )

    assert diagnostic.phase == "setup"
    assert diagnostic.code == "connected_setup_failed"
    assert diagnostic.error_type == "ExternalError"
    assert "ASCII_PRIVATE_PROMPT_MARKER_639" not in str(diagnostic)
