"""BIZ-628 — contract-declared bounded fact projection 회귀."""

from __future__ import annotations

import pytest

from simpleclaw.agent.composition_projection import (
    CompositionProjectionError,
    build_composition_input,
)
from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    ContractDescriptorV1,
    ContractRefV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus


def _values():
    owner = AssetRefV1(type="recipe", name="sports-live")
    ref = ContractRefV1(
        contract_id="recipe.sports-live.output",
        version="1",
        owner_ref=owner,
        schema_hash="schema-hash",
    )
    descriptor = ContractDescriptorV1(
        ref=ref,
        json_schema={
            "type": "object",
            "properties": {
                "data": {
                    "properties": {
                        "season": {"properties": {"code": {}}},
                        "items": {
                            "type": "array",
                            "items": {
                                "properties": {
                                    "rank": {},
                                    "team": {},
                                    "wins": {},
                                }
                            },
                        },
                    }
                }
            },
            "x-simpleclaw-composition-fields": [
                "data.season.code",
                "data.items[*].rank",
                "data.items[*].team",
                "data.items[*].wins",
            ],
        },
    )
    payload = {
        "schema": "asset_result.v1",
        "status": "completed",
        "side_effect": False,
        "data": {
            "season": {"code": "2026"},
            "items": [
                {
                    "rank": 1,
                    "team": "KT",
                    "wins": 59,
                    "private": {"token": "SECRET"},
                }
            ],
            "answer": "ASSET FINAL",
            "error": {"provider_payload": "RAW"},
        },
        "resolved_claims": ["standings"],
        "unresolved_claims": [],
    }
    result = NormalizedAssetResultV1(
        invocation_id="invocation",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload=payload,
        payload_hash="payload-hash",
        effect_status=EffectStatus.NONE,
    )
    return descriptor, result


def test_projection_includes_only_declared_typed_facts() -> None:
    descriptor, result = _values()

    projection = build_composition_input(
        request_id="telegram:42:1",
        question="현재 KBO 상위 3팀만 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        normalized_result=result,
        descriptor=descriptor,
    )

    assert projection.public_facts == {
        "data": {
            "season": {"code": "2026"},
            "items": [{"rank": 1, "team": "KT", "wins": 59}],
        }
    }
    serialized = projection.model_dump_json(by_alias=True)
    assert "ASSET FINAL" not in serialized
    assert "SECRET" not in serialized
    assert "RAW" not in serialized


def test_projection_activates_descriptor_declared_neutral_relation() -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")
    ref = ContractRefV1(
        contract_id="recipe.neutral-records.output",
        version="1",
        owner_ref=owner,
        schema_hash="neutral-schema-hash",
    )
    descriptor = ContractDescriptorV1(
        ref=ref,
        json_schema={
            "type": "object",
            "properties": {
                "data": {
                    "properties": {
                        "state": {},
                        "records": {
                            "type": "array",
                            "items": {"properties": {"state": {}}},
                        },
                    }
                }
            },
            "x-simpleclaw-composition-fields": [
                "data.state",
                "data.records[*].state",
            ],
            "x-simpleclaw-structural-evidence-relations": [
                {
                    "when": {"path": "data.state", "equals": "absent"},
                    "evidence_fields": ["data.state"],
                    "evidence_must_be_visible": False,
                }
            ],
        },
    )
    result = NormalizedAssetResultV1(
        invocation_id="neutral-invocation",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {"state": "absent", "records": []},
        },
        payload_hash="neutral-payload-hash",
        effect_status=EffectStatus.NONE,
    )

    projection = build_composition_input(
        request_id="request",
        question="Are records available?",
        locale="en-US",
        selected_route="recipe",
        normalized_result=result,
        descriptor=descriptor,
    )

    assert [
        item.model_dump() for item in projection.structural_evidence_relations
    ] == [
        {
            "evidence_paths": ("data.state",),
            "evidence_must_be_visible": False,
            "allowed_scope_words": ("Are", "records", "available"),
        }
    ]

    nonmatching = NormalizedAssetResultV1(
        invocation_id="ready-invocation",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {"state": "ready", "records": []},
        },
        payload_hash="ready-payload-hash",
        effect_status=EffectStatus.NONE,
    )
    nonmatching_projection = build_composition_input(
        request_id="request-ready",
        question="Are records available?",
        locale="en-US",
        selected_route="recipe",
        normalized_result=nonmatching,
        descriptor=descriptor,
    )
    assert nonmatching_projection.structural_evidence_relations == ()


def test_descriptor_rejects_relation_evidence_outside_projection() -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")

    with pytest.raises(ValueError, match="composition-visible"):
        ContractDescriptorV1(
            ref=ContractRefV1(
                contract_id="recipe.neutral-records.output",
                version="1",
                owner_ref=owner,
                schema_hash="neutral-schema-hash",
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "properties": {"state": {}, "reason": {}}
                    }
                },
                "x-simpleclaw-composition-fields": ["data.state"],
                "x-simpleclaw-structural-evidence-relations": [
                    {
                        "when": {"path": "data.state", "equals": "absent"},
                        "evidence_fields": ["data.reason"],
                        "evidence_must_be_visible": False,
                    }
                ],
            },
        )


def test_structural_relation_expands_all_wildcard_evidence_in_index_order() -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")
    ref = ContractRefV1(
        contract_id="recipe.neutral-records.output",
        version="1",
        owner_ref=owner,
        schema_hash="neutral-wildcard-hash",
    )
    descriptor = ContractDescriptorV1(
        ref=ref,
        json_schema={
            "type": "object",
            "properties": {
                "data": {
                    "properties": {
                        "phase": {},
                        "records": {
                            "type": "array",
                            "items": {"properties": {"value": {}}},
                        },
                    }
                }
            },
            "x-simpleclaw-composition-fields": [
                "data.phase",
                "data.records[*].value",
            ],
            "x-simpleclaw-structural-evidence-relations": [
                {
                    "when": {"path": "data.phase", "equals": "ready"},
                    "evidence_fields": ["data.records[*].value"],
                    "evidence_must_be_visible": True,
                }
            ],
        },
    )
    result = NormalizedAssetResultV1(
        invocation_id="neutral-wildcard",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {
                "phase": "ready",
                "records": [{"value": "alpha"}, {"value": "beta"}],
            },
        },
        payload_hash="neutral-wildcard-payload-hash",
        effect_status=EffectStatus.NONE,
    )

    projection = build_composition_input(
        request_id="neutral-wildcard-request",
        question="What are the two record values?",
        locale="en-US",
        selected_route="recipe",
        normalized_result=result,
        descriptor=descriptor,
    )

    assert projection.structural_evidence_relations[0].evidence_paths == (
        "data.records[0].value",
        "data.records[1].value",
    )


@pytest.mark.parametrize(
    ("second_policy", "message"),
    [
        (False, "duplicate structural evidence relation"),
        (True, "conflicting structural evidence relation"),
    ],
)
def test_descriptor_rejects_duplicate_or_conflicting_structural_relations(
    second_policy: bool,
    message: str,
) -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")
    relation = {
        "when": {"path": "data.phase", "equals": "ready"},
        "evidence_fields": ["data.value"],
        "evidence_must_be_visible": False,
    }

    with pytest.raises(ValueError, match=message):
        ContractDescriptorV1(
            ref=ContractRefV1(
                contract_id="recipe.neutral-records.output",
                version="1",
                owner_ref=owner,
                schema_hash="neutral-conflict-hash",
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "data": {"properties": {"phase": {}, "value": {}}}
                },
                "x-simpleclaw-composition-fields": [
                    "data.phase",
                    "data.value",
                ],
                "x-simpleclaw-structural-evidence-relations": [
                    relation,
                    {
                        **relation,
                        "evidence_must_be_visible": second_policy,
                    },
                ],
            },
        )


def test_projection_rejects_contract_confusion_and_unsafe_effect() -> None:
    descriptor, result = _values()
    other_owner = AssetRefV1(type="recipe", name="other")
    other = descriptor.model_copy(
        update={
            "ref": descriptor.ref.model_copy(update={"owner_ref": other_owner})
        }
    )

    with pytest.raises(CompositionProjectionError, match="contract_mismatch"):
        build_composition_input(
            request_id="request",
            question="질문",
            locale="ko-KR",
            selected_route="recipe",
            normalized_result=result,
            descriptor=other,
        )

    unsafe = result.model_copy(update={"effect_status": EffectStatus.UNKNOWN})
    with pytest.raises(CompositionProjectionError, match="effect_not_safe"):
        build_composition_input(
            request_id="request",
            question="질문",
            locale="ko-KR",
            selected_route="recipe",
            normalized_result=unsafe,
            descriptor=descriptor,
        )


@pytest.mark.parametrize(
    "path",
    ["data.answer", "data.private.token", "data.error.provider_payload", "data.raw"],
)
def test_descriptor_rejects_presentation_and_private_paths(path: str) -> None:
    owner = AssetRefV1(type="recipe", name="fixture")
    with pytest.raises(ValueError, match="forbidden presentation"):
        ContractDescriptorV1(
            ref=ContractRefV1(
                contract_id="fixture.output",
                version="1",
                owner_ref=owner,
                schema_hash="hash",
            ),
            json_schema={
                "type": "object",
                "x-simpleclaw-composition-fields": [path],
            },
        )


@pytest.mark.parametrize(
    "path",
    ["data.safeMissing", "other.value"],
)
def test_descriptor_rejects_paths_not_declared_by_schema(path: str) -> None:
    owner = AssetRefV1(type="recipe", name="fixture")
    with pytest.raises(ValueError, match="not declared by JSON Schema"):
        ContractDescriptorV1(
            ref=ContractRefV1(
                contract_id="fixture.output",
                version="1",
                owner_ref=owner,
                schema_hash="hash",
            ),
            json_schema={
                "type": "object",
                "properties": {"data": {"properties": {"safe": {}}}},
                "x-simpleclaw-composition-fields": [path],
            },
        )


@pytest.mark.parametrize(
    "field",
    ["password", "apiKey", "credentials", "email", "internalPrompt"],
)
def test_descriptor_rejects_declared_private_fields(field: str) -> None:
    owner = AssetRefV1(type="recipe", name="fixture")
    with pytest.raises(ValueError, match="forbidden presentation"):
        ContractDescriptorV1(
            ref=ContractRefV1(
                contract_id="fixture.output",
                version="1",
                owner_ref=owner,
                schema_hash="hash",
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "data": {"properties": {field: {"type": "string"}}}
                },
                "x-simpleclaw-composition-fields": [f"data.{field}"],
            },
        )
