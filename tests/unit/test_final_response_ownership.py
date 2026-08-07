"""BIZ-628 — typed asset의 최종 발화 직접 소유 금지 회귀."""

from simpleclaw.agent.asset_result_presentation import (
    SAFE_EMPTY_RESULT,
    compose_user_facing_asset_result,
)


def test_typed_asset_answer_is_not_a_final_response_source() -> None:
    payload = {
        "schema": "asset_result.v1",
        "status": "completed",
        "side_effect": False,
        "answer": "ASSET_OWNED_FINAL_MUST_NOT_RENDER",
        "data": {
            "items": [{"rank": 1, "team": "KT", "wins": 59}],
            "answer": "NESTED_ASSET_FINAL_MUST_NOT_RENDER",
        },
    }

    rendered = compose_user_facing_asset_result(
        payload=payload,
        result_status="resolved",
        effect_status="none",
    )

    assert rendered == SAFE_EMPTY_RESULT
    assert "ASSET_OWNED" not in rendered
    assert "NESTED_ASSET" not in rendered


def test_non_typed_legacy_answer_remains_compatible() -> None:
    assert compose_user_facing_asset_result(
        payload={"answer": "legacy final"},
        result_status="resolved",
        effect_status="none",
    ) == "legacy final"
