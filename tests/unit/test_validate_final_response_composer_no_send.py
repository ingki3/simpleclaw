"""BIZ-641 — connected 2-scenario no-send validator의 offline 회귀."""

from __future__ import annotations

import json

import pytest

from scripts.dev.validate_final_response_composer_no_send import (
    _SCENARIOS,
    _connected_probe,
    _ConnectedProbeFacade,
    _OneCallSend,
)
from simpleclaw.agent.final_response_composer import FinalResponseComposer
from simpleclaw.graph_runtime.adapters.delivery import (
    CronDeliveryAdapter,
    TelegramDeliveryAdapter,
)
from simpleclaw.graph_runtime.runtime import (
    InMemoryDeliveryJournal,
    ShadowBudgetUsageV1,
)
from simpleclaw.llm.models import LLMResponse


async def _draft_response(request) -> LLMResponse:
    citation_paths = request.response_schema["properties"]["cited_paths"]["items"][
        "enum"
    ]
    if len(citation_paths) > 1:
        citation_paths = sorted(
            citation_paths,
            key=lambda path: (
                int(path.split("[")[1].split("]")[0]),
                0 if path.endswith(".name") else 1,
            ),
        )
    content = (
        "The activation gate is READY."
        if len(citation_paths) == 1
        else "Alpha One, Beta Two, Gamma Three."
    )
    return LLMResponse(
        text=json.dumps(
            {
                "content": content,
                "cited_paths": citation_paths,
                "limitation_paths": [],
            }
        )
    )


@pytest.mark.parametrize("scenario", _SCENARIOS, ids=lambda item: item.name)
@pytest.mark.asyncio
async def test_connected_probe_measures_configured_sink_deltas(scenario) -> None:
    counted_send = _OneCallSend(_draft_response)
    composer = FinalResponseComposer(
        send=counted_send,
        persona_prompt="Answer only from the supplied facts.",
        max_tokens=1200,
        backend_name="offline-fixture",
    )

    result = await _connected_probe(
        composer=composer,
        counted_send=counted_send,
        scenario=scenario,
        timeout=10.0,
    )

    assert result["name"] == scenario.name
    assert result["provider_calls"] == 1
    assert result["retry_calls"] == 0
    assert result["composer_calls"] == 1
    assert result["guard_accepted"] is True
    assert set(result["citations"]) == set(scenario.resolved_claims)
    assert result["sink_spy_preflight_calls"] == {
        "telegram_send": 1,
        "notifier": 1,
        "conversation_write": 1,
        "supporting_dispatch": 1,
    }
    assert result["measured_forbidden_boundary_calls"] == {
        "telegram_send": 0,
        "notifier": 0,
        "conversation_write": 0,
        "supporting_dispatch": 0,
    }
    assert result["measured_side_effect_deltas"] == {
        "telegram_send": 0,
        "conversation_write": 0,
        "notifier": 0,
    }
    assert result["conversation_store_message_delta"] == 0


def test_probe_facade_connects_production_delivery_adapter_types(tmp_path) -> None:
    facade = _ConnectedProbeFacade(
        architecture="langgraph_v4",
        mode="primary",
        shadow_no_send=True,
        budget=ShadowBudgetUsageV1(
            max_graph_steps=1,
            max_asset_calls=1,
            max_llm_calls=1,
            max_tokens=1,
            max_seconds=1.0,
            max_parallel_invocations=1,
            graph_steps=0,
            asset_calls=0,
            llm_calls=0,
            tokens=0,
            elapsed_seconds=0.0,
            parallel_peak=0,
            stop_condition="completed",
        ),
        checkpoint_path=tmp_path / "checkpoint.sqlite3",
        telegram_adapter=TelegramDeliveryAdapter(lambda *_args: None),
        notifier_adapter=CronDeliveryAdapter(lambda *_args: None),
    )

    runtime = facade.shadow_delivery_runtime(InMemoryDeliveryJournal())

    assert isinstance(runtime._adapters["telegram"], TelegramDeliveryAdapter)
    assert isinstance(runtime._adapters["cron"], CronDeliveryAdapter)
