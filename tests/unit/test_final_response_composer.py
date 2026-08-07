"""BIZ-628 — 중앙 persona-aware composer request 경계."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent.composition_contracts import CompositionInputV1
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
    assert draft.content.startswith("현재 KBO")


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
