"""V4 primary original/effective plan diagnostic 회귀."""

from dataclasses import replace

import pytest

from simpleclaw.agent.orchestrator import (
    _log_langgraph_v4_primary_isolated,
    _log_unified_turn_planner_effective,
    _selected_asset_hash,
    _selected_asset_kind,
)
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
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

    assert _selected_asset_kind(original) == "none"
    assert _selected_asset_kind(effective) == "recipe"


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
        selected_asset_kind="recipe",
        selected_asset_hash="a" * 64,
        catalog_fingerprint="b" * 64,
        registry_fingerprint="c" * 64,
        owned_input_contract_present=False,
        owned_output_contract_present=False,
        owned_binding_present=False,
    )

    assert diagnostic.phase == "registry_lookup"
    assert diagnostic.code == "asset_not_registered_read_only"
    assert diagnostic.error_type == "ValueError"
    assert diagnostic.selected_asset_kind == "recipe"
    assert diagnostic.selected_asset_hash == "a" * 64
    assert diagnostic.catalog_fingerprint == "b" * 64
    assert diagnostic.registry_fingerprint == "c" * 64
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


@pytest.mark.parametrize(
    "marker_field",
    [
        "original_asset",
        "effective_asset",
        "original_fingerprint",
        "effective_fingerprint",
        "diagnostic_kind",
        "selected_hash",
        "approved_hash",
        "catalog_fingerprint",
        "catalog_fallback_fingerprint",
        "registry_fingerprint",
        "exception_message",
    ],
)
def test_structured_fields_are_closed_or_hashed_before_formatter(
    marker_field: str,
    caplog,
) -> None:
    marker = "ASCII_ASSET_IDENTITY_MARKER_614"
    original_name = (
        f"original-{marker}"
        if marker_field == "original_asset"
        else "original-workflow"
    )
    effective_name = (
        f"effective-{marker}"
        if marker_field == "effective_asset"
        else "effective-workflow"
    )
    assets = tuple(
        PlannerAsset(
            asset_type="recipe",
            name=name,
            description="benign catalog fixture",
            domains=("general",),
            intents=("lookup",),
            read_only=True,
            side_effects=False,
            freshness_sensitive=False,
            direct_answer=True,
            requires_confirmation=False,
            output_contract="fixture",
            declared=True,
            runtime_visible=True,
            definition_fingerprint=definition_hash,
        )
        for name, definition_hash in (
            (
                original_name,
                marker if marker_field == "original_fingerprint" else "d" * 64,
            ),
            (
                effective_name,
                marker if marker_field == "effective_fingerprint" else "f" * 64,
            ),
        )
    )
    catalog = PlannerCatalog(
        assets=assets,
        fingerprint=(
            marker
            if marker_field == "catalog_fallback_fingerprint"
            else "e" * 64
        ),
    )
    base = _asset_zero_plan()
    original = replace(
        base,
        capability=replace(
            base.capability,
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", original_name),
        ),
    )
    effective = replace(
        base,
        capability=replace(
            base.capability,
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", effective_name),
        ),
    )
    diagnostic = ConnectedExecutionError(
        "binding",
        ValueError(marker if marker_field == "exception_message" else "failure"),
        selected_asset_kind=(
            marker if marker_field == "diagnostic_kind" else "recipe"
        ),
        selected_asset_hash=marker if marker_field == "selected_hash" else "a" * 64,
        approved_asset_hash=marker if marker_field == "approved_hash" else "9" * 64,
        catalog_fingerprint=(
            marker
            if marker_field
            in {"catalog_fingerprint", "catalog_fallback_fingerprint"}
            else "b" * 64
        ),
        registry_fingerprint=(
            marker if marker_field == "registry_fingerprint" else "c" * 64
        ),
    )

    with caplog.at_level("ERROR", logger="simpleclaw.agent.orchestrator"):
        _log_langgraph_v4_primary_isolated(
            request_id="formatter-request",
            original_plan=original,
            effective_plan=effective,
            catalog=catalog,
            diagnostic=diagnostic,
        )

    formatted = caplog.text
    assert formatted.count(marker) == 0
    assert "original_asset_kind=recipe" in formatted
    assert "effective_asset_kind=recipe" in formatted
    expected_original_hash = "" if marker_field == "original_fingerprint" else "d" * 64
    expected_effective_hash = "" if marker_field == "effective_fingerprint" else "f" * 64
    assert f"original_asset_hash={expected_original_hash} " in formatted
    assert f"effective_asset_hash={expected_effective_hash} " in formatted
    expected_catalog_fingerprint = {
        "catalog_fingerprint": "e" * 64,
        "catalog_fallback_fingerprint": "",
    }.get(marker_field, "b" * 64)
    assert f"catalog_fingerprint={expected_catalog_fingerprint} " in formatted
    assert "error_message=message_sha256=" in formatted
    assert _selected_asset_hash(effective, catalog) == expected_effective_hash


@pytest.mark.parametrize("invalid_asset", ["original", "effective"])
def test_effective_plan_info_log_rejects_invalid_catalog_fingerprint(
    invalid_asset: str,
    caplog,
) -> None:
    marker = "ASCII_FINGERPRINT_MARKER_614"
    base = _asset_zero_plan()
    original = replace(
        base,
        capability=replace(
            base.capability,
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", "original-workflow"),
        ),
    )
    effective = replace(
        base,
        capability=replace(
            base.capability,
            coverage=CapabilityCoverage.FULL,
            primary_asset=AssetRef("recipe", "effective-workflow"),
        ),
    )
    catalog = PlannerCatalog(
        assets=tuple(
            PlannerAsset(
                asset_type="recipe",
                name=name,
                description="benign catalog fixture",
                domains=("general",),
                intents=("lookup",),
                read_only=True,
                side_effects=False,
                freshness_sensitive=False,
                direct_answer=True,
                requires_confirmation=False,
                output_contract="fixture",
                declared=True,
                runtime_visible=True,
                definition_fingerprint=(
                    marker if invalid_asset == asset else valid_fingerprint
                ),
            )
            for asset, name, valid_fingerprint in (
                ("original", "original-workflow", "d" * 64),
                ("effective", "effective-workflow", "f" * 64),
            )
        ),
        fingerprint="e" * 64,
    )

    with caplog.at_level("INFO", logger="simpleclaw.agent.orchestrator"):
        _log_unified_turn_planner_effective(
            request_id="formatter-request",
            original_plan=original,
            effective_plan=effective,
            catalog=catalog,
        )

    formatted = caplog.text
    assert marker not in formatted
    assert f"{invalid_asset}_asset_hash= " in formatted
