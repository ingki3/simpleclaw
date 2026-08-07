"""BIZ-618 — connected V4 typed item renderer 보안 회귀."""

from __future__ import annotations

import pytest

from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    ContractRefV1,
    NormalizedAssetResultV1,
)
from simpleclaw.graph_runtime.shadow import _compose_user_facing_result
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus


def _result(
    payload: dict[str, object],
    *,
    status: AssetResultStatus = AssetResultStatus.RESOLVED,
    effect_status: EffectStatus = EffectStatus.NONE,
) -> NormalizedAssetResultV1:
    owner = AssetRefV1(type="recipe", name="fixture")
    return NormalizedAssetResultV1(
        invocation_id="renderer-invocation",
        output_contract=ContractRefV1(
            contract_id="fixture.output",
            version="1",
            owner_ref=owner,
            schema_hash="fixture-output-hash",
        ),
        status=status,
        payload=payload,
        payload_hash="fixture-payload-hash",
        effect_status=effect_status,
    )


def _typed_payload(*, side_effect: object = False) -> dict[str, object]:
    return {
        "schema": "asset_result.v1",
        "status": "completed",
        "side_effect": side_effect,
        "data": {
            "ok": True,
            "items": [
                {"rank": 1, "team": "LG", "wins": 60, "private": {"token": "SECRET"}},
                {"rank": 2, "team": "한화", "wins": 58},
                {"rank": 3, "team": "롯데", "wins": 55},
            ],
            "error": {"code": "RAW_ERROR_SHOULD_NOT_RENDER"},
        },
        "error": "RAW_TOP_LEVEL_ERROR_SHOULD_NOT_RENDER",
    }


def test_preferred_text_path_wins_before_structured_items() -> None:
    payload = _typed_payload()
    payload["answer"] = "기존 preferred answer"

    assert _compose_user_facing_result(_result(payload)) == "기존 preferred answer"


def test_verified_read_only_typed_items_render_allowlisted_scalars_only() -> None:
    rendered = _compose_user_facing_result(_result(_typed_payload()))

    assert rendered == (
        "확인된 결과입니다.\n"
        "- 순위: 1 · 팀: LG · 승: 60\n"
        "- 순위: 2 · 팀: 한화 · 승: 58\n"
        "- 순위: 3 · 팀: 롯데 · 승: 55"
    )
    assert "SECRET" not in rendered
    assert "schema" not in rendered
    assert "status" not in rendered
    assert "RAW_" not in rendered


@pytest.mark.parametrize(
    ("payload_mutation", "effect_status"),
    [
        ({"side_effect": True}, EffectStatus.NONE),
        ({"side_effect": None}, EffectStatus.NONE),
        ({"status": "unknown_effect"}, EffectStatus.NONE),
        ({"side_effect": False}, EffectStatus.CONFIRMATION_REQUIRED),
    ],
    ids=("reported-effect", "missing-effect", "unsafe-status", "effect-gate"),
)
def test_unsafe_typed_items_fail_closed_without_raw_diagnostics(
    payload_mutation: dict[str, object],
    effect_status: EffectStatus,
) -> None:
    payload = _typed_payload()
    if (
        "side_effect" in payload_mutation
        and payload_mutation["side_effect"] is None
    ):
        payload.pop("side_effect")
    else:
        payload.update(payload_mutation)

    rendered = _compose_user_facing_result(
        _result(payload, effect_status=effect_status)
    )

    assert rendered == (
        "요청을 처리했지만 안전하게 표시할 수 있는 텍스트 결과가 없습니다."
    )
    assert "asset_result.v1" not in rendered
    assert "unknown_effect" not in rendered
    assert "RAW_" not in rendered


def test_structured_item_output_is_deterministically_bounded() -> None:
    payload = _typed_payload()
    payload["data"] = {
        "ok": True,
        "items": [
            {"rank": index, "team": "가" * 500, "nested": {"secret": "SECRET"}}
            for index in range(1, 40)
        ],
    }

    rendered = _compose_user_facing_result(_result(payload))

    assert len(rendered) <= 3_500
    assert rendered.count("\n-") <= 20
    assert "…" in rendered
    assert "SECRET" not in rendered
