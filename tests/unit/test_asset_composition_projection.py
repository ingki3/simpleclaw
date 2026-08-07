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
