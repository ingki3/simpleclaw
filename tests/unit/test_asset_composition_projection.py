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
            "identity_paths": (),
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


@pytest.mark.parametrize("evidence", [" ready ", "ready ", " ready"])
def test_projection_rejects_edge_whitespace_structural_evidence(
    evidence: str,
) -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")
    ref = ContractRefV1(
        contract_id="recipe.neutral-records.output",
        version="1",
        owner_ref=owner,
        schema_hash="neutral-edge-whitespace-hash",
    )
    descriptor = ContractDescriptorV1(
        ref=ref,
        json_schema={
            "type": "object",
            "properties": {
                "data": {
                    "properties": {
                        "phase": {},
                        "state": {},
                    }
                }
            },
            "x-simpleclaw-composition-fields": ["data.phase", "data.state"],
            "x-simpleclaw-structural-evidence-relations": [
                {
                    "when": {"path": "data.phase", "equals": "active"},
                    "evidence_fields": ["data.state"],
                }
            ],
        },
    )
    result = NormalizedAssetResultV1(
        invocation_id="neutral-edge-whitespace",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {"phase": "active", "state": evidence},
        },
        payload_hash="neutral-edge-whitespace-payload-hash",
        effect_status=EffectStatus.NONE,
    )

    with pytest.raises(
        CompositionProjectionError,
        match="projection.structural_evidence_not_renderable",
    ):
        build_composition_input(
            request_id="neutral-edge-whitespace-request",
            question="What is the record state?",
            locale="en-US",
            selected_route="recipe",
            normalized_result=result,
            descriptor=descriptor,
        )


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
                    }
                ],
            },
        )


def test_descriptor_rejects_relation_condition_outside_projection() -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")

    with pytest.raises(ValueError, match="condition must be composition-visible"):
        ContractDescriptorV1(
            ref=ContractRefV1(
                contract_id="recipe.neutral-records.output",
                version="1",
                owner_ref=owner,
                schema_hash="neutral-hidden-condition-hash",
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "properties": {"phase": {}, "state": {}}
                    }
                },
                "x-simpleclaw-composition-fields": ["data.state"],
                "x-simpleclaw-structural-evidence-relations": [
                    {
                        "when": {"path": "data.phase", "equals": "ready"},
                        "evidence_fields": ["data.state"],
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
                    "identity_fields": ["data.records[*].value"],
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
    assert projection.structural_evidence_relations[0].identity_paths == (
        "data.records[0].value",
        "data.records[1].value",
    )

    sparse = NormalizedAssetResultV1(
        invocation_id="neutral-sparse-wildcard",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {
                "phase": "ready",
                "records": [{"value": "alpha"}, {}],
            },
        },
        payload_hash="neutral-sparse-wildcard-payload-hash",
        effect_status=EffectStatus.NONE,
    )
    with pytest.raises(
        CompositionProjectionError,
        match="structural_evidence_incomplete",
    ):
        build_composition_input(
            request_id="neutral-sparse-wildcard-request",
            question="What are the two record values?",
            locale="en-US",
            selected_route="recipe",
            normalized_result=sparse,
            descriptor=descriptor,
        )

    empty = NormalizedAssetResultV1(
        invocation_id="neutral-empty-wildcard",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {"phase": "ready", "records": []},
        },
        payload_hash="neutral-empty-wildcard-payload-hash",
        effect_status=EffectStatus.NONE,
    )
    with pytest.raises(
        CompositionProjectionError,
        match="structural_evidence_empty",
    ):
        build_composition_input(
            request_id="neutral-empty-wildcard-request",
            question="What are the record values?",
            locale="en-US",
            selected_route="recipe",
            normalized_result=empty,
            descriptor=descriptor,
        )

    fallback_descriptor = ContractDescriptorV1(
        ref=ref,
        json_schema={
            **descriptor.json_schema,
            "properties": {
                "data": {
                    "properties": {
                        "phase": {},
                        "state": {},
                        "records": {
                            "type": "array",
                            "items": {"properties": {"value": {}}},
                        },
                    }
                }
            },
            "x-simpleclaw-composition-fields": [
                "data.phase",
                "data.state",
                "data.records[*].value",
            ],
            "x-simpleclaw-structural-evidence-relations": [
                *descriptor.json_schema[
                    "x-simpleclaw-structural-evidence-relations"
                ],
                {
                    "when": {"path": "data.state", "equals": "absent"},
                    "evidence_fields": ["data.state"],
                },
            ],
        },
    )
    fallback_result = NormalizedAssetResultV1(
        invocation_id="neutral-empty-wildcard-fallback",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {"phase": "ready", "state": "absent", "records": []},
        },
        payload_hash="neutral-empty-wildcard-fallback-payload-hash",
        effect_status=EffectStatus.NONE,
    )

    fallback_projection = build_composition_input(
        request_id="neutral-empty-wildcard-fallback-request",
        question="Are records available?",
        locale="en-US",
        selected_route="recipe",
        normalized_result=fallback_result,
        descriptor=fallback_descriptor,
    )

    assert fallback_projection.structural_evidence_relations[0].evidence_paths == (
        "data.state",
    )


@pytest.mark.parametrize(
    ("when_equals", "payload_value", "active"),
    [
        (True, 1, False),
        (False, 0, False),
        (True, True, True),
        (None, None, True),
        (1, 1, True),
        (1, 1.0, True),
        ("1", "1", True),
    ],
)
def test_structural_relation_condition_uses_json_scalar_identity(
    when_equals: object,
    payload_value: object,
    active: bool,
) -> None:
    owner = AssetRefV1(type="recipe", name="neutral-condition")
    ref = ContractRefV1(
        contract_id="recipe.neutral-condition.output",
        version="1",
        owner_ref=owner,
        schema_hash="neutral-condition-hash",
    )
    descriptor = ContractDescriptorV1(
        ref=ref,
        json_schema={
            "type": "object",
            "properties": {
                "data": {"properties": {"condition": {}, "value": {}}}
            },
            "x-simpleclaw-composition-fields": ["data.condition", "data.value"],
            "x-simpleclaw-structural-evidence-relations": [
                {
                    "when": {"path": "data.condition", "equals": when_equals},
                    "evidence_fields": ["data.value"],
                }
            ],
        },
    )
    result = NormalizedAssetResultV1(
        invocation_id="neutral-condition",
        output_contract=ref,
        status=AssetResultStatus.RESOLVED,
        payload={
            "status": "completed",
            "side_effect": False,
            "data": {"condition": payload_value, "value": "visible"},
        },
        payload_hash="neutral-condition-payload-hash",
        effect_status=EffectStatus.NONE,
    )

    projection = build_composition_input(
        request_id="neutral-condition-request",
        question="What is the value?",
        locale="en-US",
        selected_route="recipe",
        normalized_result=result,
        descriptor=descriptor,
    )

    assert bool(projection.structural_evidence_relations) is active


@pytest.mark.parametrize(
    ("second_fields", "message"),
    [
        (
            ["data.other", "data.value"],
            "duplicate structural evidence relation",
        ),
        (["data.value"], "conflicting structural evidence relation"),
    ],
)
def test_descriptor_rejects_duplicate_or_conflicting_structural_relations(
    second_fields: list[str],
    message: str,
) -> None:
    owner = AssetRefV1(type="recipe", name="neutral-records")
    relation = {
        "when": {"path": "data.phase", "equals": "ready"},
        "evidence_fields": ["data.value", "data.other"],
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
                    "data": {
                        "properties": {
                            "phase": {},
                            "value": {},
                            "other": {},
                        }
                    }
                },
                "x-simpleclaw-composition-fields": [
                    "data.phase",
                    "data.value",
                    "data.other",
                ],
                "x-simpleclaw-structural-evidence-relations": [
                    relation,
                    {
                        **relation,
                        "evidence_fields": second_fields,
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
