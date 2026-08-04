"""BIZ-564 — domain-neutral V4 contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from simpleclaw.graph_runtime.contracts import (
    AssetInvocationV1,
    AssetRefV1,
    ContractRefV1,
    ExecutionBudgetV1,
    ExecutionPlanV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.status import AssetResultStatus


def _asset(name: str = "fixture") -> AssetRefV1:
    return AssetRefV1(type="extension", name=name)


def _contract(asset: AssetRefV1, contract_id: str) -> ContractRefV1:
    return ContractRefV1(
        contract_id=contract_id,
        version="1",
        owner_ref=asset,
        schema_hash=f"hash-{contract_id}",
    )


def _invocation(invocation_id: str = "invoke-1") -> AssetInvocationV1:
    asset = _asset()
    return AssetInvocationV1(
        invocation_id=invocation_id,
        asset_ref=asset,
        definition_fingerprint="definition-hash",
        input_contract=_contract(asset, "input"),
        payload={"arbitrary": {"nested": [1, "two", True, None]}},
        payload_hash="payload-hash",
        output_contract=_contract(asset, "output"),
    )


def test_opaque_payload_round_trips_without_domain_fields() -> None:
    invocation = _invocation()
    restored = AssetInvocationV1.model_validate_json(invocation.model_dump_json())

    assert restored == invocation
    assert restored.payload == {"arbitrary": {"nested": [1, "two", True, None]}}

    result = NormalizedAssetResultV1(
        invocation_id=invocation.invocation_id,
        output_contract=invocation.output_contract,
        status=AssetResultStatus.RESOLVED,
        payload=invocation.payload,
        payload_hash=invocation.payload_hash,
    )
    assert result.payload_hash == invocation.payload_hash


@pytest.mark.parametrize("missing", ["owner_ref", "schema_hash"])
def test_contract_reference_requires_owner_and_schema_hash(missing: str) -> None:
    raw = {
        "contract_id": "input",
        "version": "1",
        "owner_ref": {"type": "extension", "name": "fixture"},
        "schema_hash": "schema-hash",
    }
    raw.pop(missing)

    with pytest.raises(ValidationError):
        ContractRefV1.model_validate(raw)


def test_contracts_reject_unknown_fields_and_python_only_payload_values() -> None:
    with pytest.raises(ValidationError):
        AssetRefV1(type="extension", name="fixture", hidden="value")

    invocation = _invocation().model_dump()
    invocation["payload"] = {"opaque": {1, 2, 3}}
    with pytest.raises(ValidationError, match="JSON"):
        AssetInvocationV1.model_validate(invocation)


def test_contract_owner_mismatch_and_dependency_cycle_fail_closed() -> None:
    invocation = _invocation()
    other = _asset("other")
    invalid = invocation.model_dump()
    invalid["output_contract"] = _contract(other, "output")
    with pytest.raises(ValidationError, match="owner"):
        AssetInvocationV1.model_validate(invalid)

    first = _invocation("first").model_copy(update={"depends_on": ("second",)})
    second = _invocation("second").model_copy(update={"depends_on": ("first",)})
    with pytest.raises(ValidationError, match="DAG"):
        ExecutionPlanV1(
            plan_id="plan-1",
            revision=1,
            request_id="request-1",
            catalog_fingerprint="catalog-hash",
            selected_route="react",
            invocations=(first, second),
            budget=ExecutionBudgetV1(
                max_graph_steps=10,
                max_asset_calls=5,
                max_llm_calls=3,
                max_tokens=2000,
                deadline_at=datetime.now(UTC) + timedelta(minutes=1),
                max_parallel_invocations=2,
            ),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_graph_steps", 0),
        ("max_asset_calls", None),
        ("max_llm_calls", float("inf")),
        ("max_tokens", "100"),
        ("max_parallel_invocations", -1),
    ],
)
def test_execution_budget_requires_every_axis_to_be_finite(field, value) -> None:
    raw = {
        "max_graph_steps": 10,
        "max_asset_calls": 5,
        "max_llm_calls": 3,
        "max_tokens": 2000,
        "deadline_at": datetime.now(UTC) + timedelta(minutes=1),
        "max_parallel_invocations": 2,
    }
    raw[field] = value
    with pytest.raises(ValidationError):
        ExecutionBudgetV1.model_validate(raw)
