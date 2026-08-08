"""BIZ-643 — production persona natural-query no-send gate offline 회귀."""

from __future__ import annotations

import json

import pytest

from scripts.dev.validate_final_response_composer_no_send import (
    _NATURAL_KBO_SCENARIO,
    _SCENARIOS,
    _connected_probe,
    _ConnectedProbeFacade,
    _OneCallSend,
    _production_persona_prompt,
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
    value = json.loads(request.user_message)
    assert value["question"] == "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘"
    content = "LG는 60, 한화는 58, 롯데는 55입니다."
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
    assert tuple(result["citations"]) == scenario.expected_citations
    assert result["canonical_citation_count"] == len(scenario.expected_citations)
    assert result["provider_citation_count"] >= len(scenario.expected_citations)
    assert "content" not in result
    assert result["pruned_citation_count"] > 0
    assert result["source_mode"] == "production_shaped_fixed"
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


def test_activation_scenarios_use_natural_question_without_local_persona() -> None:
    assert len(_SCENARIOS) == 3
    assert all(
        scenario.question == "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘"
        for scenario in _SCENARIOS
    )
    assert all(
        scenario.name == f"production_persona_natural_kbo_{index}"
        for index, scenario in enumerate(_SCENARIOS, start=1)
    )
    assert not hasattr(_NATURAL_KBO_SCENARIO, "persona_prompt")


def test_production_persona_uses_configured_soul_assembly_only(tmp_path) -> None:
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "SOUL.md").write_text(
        "# SOUL\n\n따뜻하고 간결한 한국어 존댓말",
        encoding="utf-8",
    )
    (persona_dir / "USER.md").write_text(
        "# USER\n\n비공개 사용자 정보",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "persona:\n"
        f"  local_dir: {persona_dir}\n"
        f"  global_dir: {tmp_path / 'missing'}\n"
        "  token_budget: 4096\n",
        encoding="utf-8",
    )

    prompt = _production_persona_prompt(config)

    assert "따뜻하고 간결한 한국어 존댓말" in prompt
    assert "비공개 사용자 정보" not in prompt


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
