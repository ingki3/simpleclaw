"""BIZ-643 — production persona natural-query no-send gate offline 회귀."""

from __future__ import annotations

import json

import pytest

from scripts.dev.validate_final_response_composer_no_send import (
    _NATURAL_KBO_SCENARIO,
    _SCENARIOS,
    _bounded_backend_names,
    _connected_probe,
    _ConnectedProbeFacade,
    _first_failure,
    _OneCallSend,
    _production_persona_projection,
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
from simpleclaw.persona.models import CompositionPersonaProjection, FileType


def _persona_projection(text: str) -> CompositionPersonaProjection:
    return CompositionPersonaProjection(
        instruction_text=text,
        source_types=(FileType.SOUL,),
        token_count=len(text.split()),
        token_budget=2048,
        policy_version="fixture_v1",
        fingerprint=f"fixture:{text}",
    )


async def _draft_response(request) -> LLMResponse:
    segment = request.response_schema["$defs"]["CompositionRenderSegmentV1"]
    allowed_paths = segment["properties"]["path"]["enum"]
    citation_paths = list(_NATURAL_KBO_SCENARIO.expected_citations)
    assert set(citation_paths) <= set(allowed_paths)
    value = json.loads(request.user_message)
    assert value["question"] == "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘"
    return LLMResponse(
        text=json.dumps(
            {
                "segments": [
                    {
                        "path": path,
                        "connector": (
                            "polite_copula_period"
                            if index == len(citation_paths) - 1
                            else "comma_space"
                        ),
                    }
                    for index, path in enumerate(citation_paths)
                ],
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
        persona_projection=_persona_projection("Answer only from the supplied facts."),
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
    assert result["temperature"] == 0.0
    assert result["guard_accepted"] is True
    assert tuple(result["citations"]) == scenario.expected_citations
    assert result["canonical_citation_count"] == len(scenario.expected_citations)
    assert result["provider_plan_path_count"] == len(scenario.expected_citations)
    assert "content" not in result
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
    assert [scenario.question for scenario in _SCENARIOS] == [
        "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
        "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
        "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
    ]
    assert [scenario.name for scenario in _SCENARIOS] == [
        "production_persona_natural_kbo_1",
        "production_persona_natural_kbo_2",
        "production_persona_natural_kbo_3",
    ]
    assert [item["wins"] for item in _SCENARIOS[0].payload["data"]["items"]] == [
        60,
        55,
        55,
    ]
    assert not hasattr(_NATURAL_KBO_SCENARIO, "persona_prompt")


def test_backend_override_is_eval_only_bounded_and_default_first() -> None:
    assert _bounded_backend_names(
        "default",
        ("alternate_a", "alternate_b", "alternate_a"),
        ("default", "alternate_a", "alternate_b"),
    ) == ("default", "alternate_a", "alternate_b")

    with pytest.raises(RuntimeError, match="limit"):
        _bounded_backend_names(
            "default",
            ("a", "b", "c"),
            ("default", "a", "b", "c"),
        )


def test_first_failure_is_preserved_after_later_pass() -> None:
    first = {
        "name": "run-1",
        "error_code": "guard_rejected",
        "provider_calls": 1,
    }
    results = [
        first,
        {"name": "run-2", "guard_accepted": True, "provider_calls": 1},
    ]

    captured = _first_failure(results)
    results[0]["error_code"] = "changed"

    assert captured == first | {"error_code": "guard_rejected"}


def test_production_persona_uses_allowlisted_runtime_projection(tmp_path) -> None:
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "SOUL.md").write_text(
        "# Identity\n\nSimpleClaw\n\n# Speaking Style\n\n따뜻하고 간결한 한국어 존댓말",
        encoding="utf-8",
    )
    (persona_dir / "USER.md").write_text(
        "# Preferences\n\n"
        "짧은 목록 선호\n"
        "Authorization: Bearer validator-fixture-token\n"
        "database_url = postgresql://fixture:password@db.invalid/app\n"
        "AWS access key: AKIAIOSFODNN7EXAMPLE\n\n"
        "# Private\n\n비공개 사용자 정보",
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

    projection = _production_persona_projection(config)

    assert "따뜻하고 간결한 한국어 존댓말" in projection.instruction_text
    assert "짧은 목록 선호" in projection.instruction_text
    assert "비공개 사용자 정보" not in projection.instruction_text
    assert "validator-fixture-token" not in projection.instruction_text
    assert "fixture:password" not in projection.instruction_text
    assert "AKIAIOSFODNN7EXAMPLE" not in projection.instruction_text
    assert projection.source_types == (FileType.SOUL, FileType.USER)


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
