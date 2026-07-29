"""BIZ-512 FunctionGemma strict function-call 계약."""

from __future__ import annotations

import pytest

from simpleclaw.evaluation.functiongemma_contract import (
    FUNCTION_DECLARATION,
    NO_ASSET,
    FunctionCallContractError,
    parse_function_call,
)


def _payload(**updates):
    value = {
        "context_relation": "standalone",
        "execution_mode": "direct_answer",
        "domains": ["technology"],
        "intents": ["explain"],
        "primary_asset": NO_ASSET,
        "fallback_required": False,
    }
    value.update(updates)
    return {"name": "classify_intent_and_select_asset", "arguments": value}


def test_schema_is_strict_and_all_fields_required() -> None:
    parameters = FUNCTION_DECLARATION["parameters"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == set(parameters["properties"])


def test_native_call_and_no_asset_parse() -> None:
    result = parse_function_call(_payload(), candidate_ids=["skill:search"])
    assert result.primary_asset == NO_ASSET
    assert result.domains == ("technology",)


def test_functiongemma_special_token_call_parses() -> None:
    payload = (
        "<start_function_call>call:classify_intent_and_select_asset{"
        "context_relation:<escape>standalone<escape>,"
        "execution_mode:<escape>execute_asset<escape>,"
        "domains:[<escape>news<escape>],"
        "intents:[<escape>lookup<escape>],"
        "primary_asset:<escape>skill:search<escape>,"
        "fallback_required:false}<end_function_call>"
    )
    result = parse_function_call(payload, candidate_ids=["skill:search"])
    assert result.primary_asset == "skill:search"
    assert result.intents == ("lookup",)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (_payload(extra=True), "schema.fields_mismatch"),
        (_payload(context_relation="invented"), "schema.invalid_context_relation"),
        (_payload(primary_asset="skill:unknown"), "boundary.unknown_asset"),
        (
            _payload(
                execution_mode="execute_asset",
                primary_asset=NO_ASSET,
                fallback_required=False,
            ),
            "boundary.missing_fallback",
        ),
    ],
)
def test_invalid_schema_and_boundary_fail_closed(payload, code: str) -> None:
    with pytest.raises(FunctionCallContractError) as exc:
        parse_function_call(payload, candidate_ids=["skill:search"])
    assert exc.value.code == code


def test_candidate_cap_and_duplicate_are_rejected() -> None:
    with pytest.raises(FunctionCallContractError, match="too_many"):
        parse_function_call(_payload(), candidate_ids=[f"skill:{i}" for i in range(13)])
    with pytest.raises(FunctionCallContractError, match="duplicate"):
        parse_function_call(_payload(), candidate_ids=["skill:a", "skill:a"])
