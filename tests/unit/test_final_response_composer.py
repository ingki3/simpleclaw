"""BIZ-628 — 중앙 persona-aware composer request 경계."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    DraftResponseV1,
)
from simpleclaw.agent.final_response_composer import FinalResponseComposer
from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus
from simpleclaw.llm.models import LLMResponse


def _input() -> CompositionInputV1:
    return CompositionInputV1(
        request_id="request-1",
        question="현재 KBO 상위 3팀만 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts={
            "data": {
                "items": [
                    {"rank": 1, "team": "KT"},
                    {"rank": 2, "team": "삼성"},
                    {"rank": 3, "team": "LG"},
                ]
            }
        },
    )


def test_draft_schema_requires_at_least_one_cited_path() -> None:
    schema = DraftResponseV1.model_json_schema(by_alias=True)

    assert "cited_paths" in schema["required"]
    assert schema["properties"]["cited_paths"]["minItems"] == 1
    assert schema["properties"]["cited_paths"]["maxItems"] == 128


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "KT입니다."},
        {"content": "KT입니다.", "cited_paths": []},
    ],
)
def test_draft_contract_rejects_missing_or_empty_citations(payload: dict) -> None:
    with pytest.raises(ValidationError):
        DraftResponseV1.model_validate(payload)


@pytest.mark.asyncio
async def test_composer_uses_persona_and_never_exposes_tools() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": "현재 KBO 상위 3팀은 KT, 삼성, LG입니다.",
                    "cited_paths": [
                        "data.items[0].team",
                        "data.items[1].team",
                        "data.items[2].team",
                    ],
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_prompt="따뜻하고 간결한 한국어 존댓말",
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_input())

    request = send.await_args.args[0]
    assert send.await_count == 1
    assert request.tools is None
    assert request.backend_name == "fixture-backend"
    assert request.route_name is None
    assert request.usage_task == "langgraph_v4_composer"
    assert request.require_structured_output is True
    assert "따뜻하고 간결한 한국어 존댓말" in request.system_prompt
    assert request.response_schema["properties"]["cited_paths"]["items"][
        "enum"
    ] == [
        "data.items[0].rank",
        "data.items[0].team",
        "data.items[1].rank",
        "data.items[1].team",
        "data.items[2].rank",
        "data.items[2].team",
    ]
    assert (
        request.response_schema["properties"]["limitation_paths"]["maxItems"]
        == 0
    )
    assert draft.content.startswith("현재 KBO")


@pytest.mark.asyncio
async def test_composer_schema_allows_only_root_relative_scalar_leaf_paths() -> None:
    value = _input().model_copy(
        update={
            "public_facts_json": (
                '{"alpha":{"empty":[],"nested":[{"active":true,"name":"A"}]},'
                '"items":[{"label":"first","value":3},'
                '{"label":"second","value":null}]}'
            ),
            "unresolved_claims": ("missing score", "missing source"),
        }
    )
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": "A입니다.",
                    "cited_paths": ["alpha.nested[0].name"],
                    "limitation_paths": [
                        "unresolved_claims[0]",
                        "unresolved_claims[1]",
                    ],
                }
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_prompt="간결하게",
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    await composer.compose(value)

    schema = send.await_args.args[0].response_schema
    assert schema["properties"]["cited_paths"]["items"]["enum"] == [
        "alpha.nested[0].active",
        "alpha.nested[0].name",
        "items[0].label",
        "items[0].value",
        "items[1].label",
        "items[1].value",
    ]
    assert schema["properties"]["limitation_paths"]["items"]["enum"] == [
        "unresolved_claims[0]",
        "unresolved_claims[1]",
    ]
    encoded = json.dumps(schema, sort_keys=True)
    assert "public_facts." not in encoded
    assert "[*]" not in encoded
    assert "alpha.empty" not in encoded
    assert '"items"' not in schema["properties"]["cited_paths"]["items"]["enum"]


@pytest.mark.asyncio
async def test_composer_rejects_more_scalar_paths_than_draft_can_cite() -> None:
    send = AsyncMock()
    composer = FinalResponseComposer(
        send=send,
        persona_prompt="간결하게",
        max_tokens=1200,
        backend_name="fixture-backend",
    )
    value = _input().model_copy(
        update={
            "public_facts_json": json.dumps(
                {f"field_{index:03d}": index for index in range(129)},
                separators=(",", ":"),
                sort_keys=True,
            )
        }
    )

    with pytest.raises(RuntimeError, match="too many citable paths"):
        await composer.compose(value)

    assert send.await_count == 0


@pytest.mark.asyncio
async def test_composer_does_not_retry_invalid_structured_output() -> None:
    send = AsyncMock(return_value=LLMResponse(text="not-json"))
    composer = FinalResponseComposer(
        send=send,
        persona_prompt="간결하게",
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    with pytest.raises(RuntimeError, match="invalid"):
        await composer.compose(_input())

    assert send.await_count == 1


@pytest.mark.asyncio
async def test_composer_does_not_retry_empty_citations() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": "현재 KBO 상위 팀은 KT입니다.",
                    "cited_paths": [],
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_prompt="간결하게",
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    with pytest.raises(RuntimeError, match="invalid"):
        await composer.compose(_input())

    assert send.await_count == 1
    request = send.await_args.args[0]
    assert "cited_paths" in request.response_schema["required"]
    assert request.response_schema["properties"]["cited_paths"]["minItems"] == 1
