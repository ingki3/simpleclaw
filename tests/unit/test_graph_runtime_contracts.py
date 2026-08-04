"""BIZ-564 — 도메인 중립 V4 계약의 불변식과 wire 호환성을 검증한다."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from simpleclaw.graph_runtime.contracts import (
    AssetInvocationV1,
    AssetRefV1,
    ContractDescriptorV1,
    ContractRefV1,
    DeliveryIntentV1,
    DraftArtifactV1,
    ExecutionBudgetV1,
    ExecutionPlanV1,
    FinalArtifactV1,
    NormalizedAssetResultV1,
    RequestEnvelopeV1,
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


def test_nested_payload_and_schema_mutation_cannot_change_canonical_contract() -> None:
    asset = _asset()
    invocation = AssetInvocationV1(
        invocation_id="invoke-immutable",
        asset_ref=asset,
        definition_fingerprint="definition-hash",
        input_contract=_contract(asset, "input"),
        payload={"nested": {"value": 1, "items": ["original"]}},
        payload_hash="payload-before",
        output_contract=_contract(asset, "output"),
    )
    descriptor = ContractDescriptorV1(
        ref=_contract(asset, "schema"),
        json_schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
        },
    )

    invocation.payload["nested"]["value"] = 2
    invocation.payload["nested"]["items"].append("mutated")
    descriptor.json_schema["properties"]["x"]["type"] = "integer"

    assert invocation.payload == {
        "nested": {"value": 1, "items": ["original"]}
    }
    assert invocation.payload_hash == "payload-before"
    assert descriptor.json_schema["properties"]["x"]["type"] == "string"
    assert descriptor.ref.schema_hash == "hash-schema"
    assert invocation.model_dump()["payload"] == invocation.payload
    assert descriptor.model_dump()["json_schema"] == descriptor.json_schema


def test_schema_wire_aliases_do_not_shadow_pydantic_api() -> None:
    contract_types = (
        RequestEnvelopeV1,
        AssetInvocationV1,
        ExecutionPlanV1,
        NormalizedAssetResultV1,
        DraftArtifactV1,
        FinalArtifactV1,
        DeliveryIntentV1,
    )

    for contract_type in contract_types:
        assert "schema" not in contract_type.model_fields
        assert contract_type.model_fields["schema_version"].alias == "schema"

    assert _invocation().model_dump()["schema"] == "asset_invocation.v1"
    assert "schema_version" not in _invocation().model_dump()


def test_contract_module_imports_with_warnings_as_errors() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            "import sys; sys.path.insert(0, 'src'); "
            "import simpleclaw.graph_runtime.contracts",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


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
