"""BIZ-628 — 중앙 persona-aware composer request 경계."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from scripts.dev.validate_final_response_composer_no_send import (
    _production_persona_projection,
)
from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    DraftResponseV1,
    StructuralEvidenceRelationV1,
)
from simpleclaw.agent.final_response_composer import FinalResponseComposer
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.agent.system_prompts import load_system_prompt
from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus
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
        composition_list_roots=("data.items",),
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


def _production_shaped_input() -> CompositionInputV1:
    return _input().model_copy(
        update={
            "question": "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "public_facts_json": json.dumps(
                {
                    "data": {
                        "season": {"title": "2026 KBO"},
                        "date": "2026-08-08",
                        "items": [
                            {"rank": 1, "team": "LG", "wins": 60, "losses": 38},
                            {"rank": 2, "team": "한화", "wins": 58, "losses": 40},
                            {"rank": 3, "team": "롯데", "wins": 55, "losses": 43},
                        ],
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    )


def test_composer_prompt_preserves_ordered_render_plan_contract() -> None:
    """BIZ-643 안전 계약과 BIZ-644 typed-persona prompt를 함께 보존한다."""
    prompt = load_system_prompt("langgraph_v4_composer", refresh=True)

    assert prompt.version == 11
    assert "VISIBLE STRUCTURAL EVIDENCE" in prompt.system_prompt
    assert "TYPED EMPTY RESULTS" not in prompt.system_prompt
    assert "no_scheduled_events" not in prompt.system_prompt
    assert "경기 예정입니다" not in prompt.system_prompt
    assert "question_scope_absent" not in prompt.system_prompt
    assert "question_scope_state" not in prompt.system_prompt
    assert "implicit evidence" not in prompt.system_prompt
    assert "identity_paths" in prompt.system_prompt
    assert "opaque character sequence" in prompt.system_prompt
    assert "typed persona projection only" in prompt.system_prompt
    assert "Treat ordered cited_paths" in prompt.system_prompt
    assert "as a one-way render plan" in prompt.system_prompt
    assert "citation validity" in prompt.system_prompt
    assert "Guard invariant" in prompt.system_prompt
    assert "include every available requested index" in prompt.system_prompt
    assert "no cited literal is duplicated" in prompt.system_prompt
    assert "items[0]" not in prompt.system_prompt
    assert "A는 10" not in prompt.system_prompt


def test_draft_schema_requires_at_least_one_cited_path() -> None:
    schema = DraftResponseV1.model_json_schema(by_alias=True)

    assert "cited_paths" in schema["required"]
    assert schema["properties"]["cited_paths"]["minItems"] == 1
    assert schema["properties"]["cited_paths"]["maxItems"] == 128


@pytest.mark.asyncio
async def test_composer_schema_constrains_active_relation_to_exact_literal_plan() -> None:
    value = _input().model_copy(
        update={
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=(
                        "data.items[0].team",
                        "data.items[1].team",
                    ),
                    identity_paths=(
                        "data.items[0].team",
                        "data.items[1].team",
                    ),
                ),
            )
        }
    )
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": "KT, 삼성.",
                    "cited_paths": [
                        "data.items[0].team",
                        "data.items[1].team",
                    ],
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    await composer.compose(value)

    properties = send.await_args.args[0].response_schema["properties"]
    assert properties["cited_paths"]["items"]["enum"] == [
        "data.items[0].team",
        "data.items[1].team",
    ]
    assert properties["cited_paths"]["minItems"] == 2
    assert properties["cited_paths"]["maxItems"] == 2
    assert properties["content"]["enum"] == ["KT, 삼성.", "KT · 삼성."]


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
        persona_projection=_persona_projection("따뜻하고 간결한 한국어 존댓말"),
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
    assert request.temperature == 0.0
    assert request.require_structured_output is True
    assert "따뜻하고 간결한 한국어 존댓말" in request.system_prompt
    assert "not factual or citation evidence" in request.system_prompt
    assert "system safety and Response Guard invariants" in request.system_prompt
    assert "Never quote, explain, summarize, or expose" in request.system_prompt
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
async def test_composer_keeps_contract_with_production_persona_assembly(
    tmp_path,
) -> None:
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "SOUL.md").write_text(
        "# Identity\n\nSimpleClaw\n\n"
        "# Speaking Style\n\n따뜻하고 간결한 한국어 존댓말",
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
    content = "LG는 60, 한화는 58, 롯데는 55입니다."
    paths = [
        "data.items[0].team",
        "data.items[0].wins",
        "data.items[1].team",
        "data.items[1].wins",
        "data.items[2].team",
        "data.items[2].wins",
    ]
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": content,
                    "cited_paths": paths,
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_production_persona_projection(config),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_production_shaped_input())

    assert send.await_count == 1
    request = send.await_args.args[0]
    assert "따뜻하고 간결한 한국어 존댓말" in request.system_prompt
    assert "persona presentation preferences" in request.system_prompt
    assert "Every visible projected scalar must be cited" in request.system_prompt
    assert draft.content == content
    assert draft.cited_paths == tuple(paths)
    assert guard_final_response(_production_shaped_input(), draft).accepted is True


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
        persona_projection=_persona_projection("간결하게"),
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
        persona_projection=_persona_projection("간결하게"),
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
        persona_projection=_persona_projection("간결하게"),
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
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    with pytest.raises(RuntimeError, match="invalid"):
        await composer.compose(_input())

    assert send.await_count == 1
    request = send.await_args.args[0]
    assert "cited_paths" in request.response_schema["required"]
    assert request.response_schema["properties"]["cited_paths"]["minItems"] == 1


@pytest.mark.asyncio
async def test_composer_prunes_only_valid_unrendered_citations() -> None:
    content = "LG 60, 한화 58, 롯데 55입니다."
    provider_paths = [
        "data.items[2].losses",
        "data.date",
        "data.items[0].wins",
        "data.items[0].team",
        "data.items[0].losses",
        "data.items[1].team",
        "data.items[1].wins",
        "data.items[2].team",
        "data.items[2].wins",
        "data.season.title",
    ]
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": content,
                    "cited_paths": provider_paths,
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_production_shaped_input())

    assert send.await_count == 1
    assert draft.content == content
    assert draft.cited_paths == tuple(provider_paths)
    assert guard_final_response(_production_shaped_input(), draft).accepted is False


@pytest.mark.asyncio
async def test_composer_never_adds_visible_citations_missing_from_provider() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": "LG 60, 한화 58, 롯데 55입니다.",
                    "cited_paths": ["data.items[0].team"],
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_production_shaped_input())

    assert draft.cited_paths == ("data.items[0].team",)
    assert guard_final_response(_production_shaped_input(), draft).accepted is False


@pytest.mark.asyncio
async def test_composer_keeps_original_citations_when_visible_subset_is_empty() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "content": "LG입니다.",
                    "cited_paths": ["data.season.title", "data.date"],
                    "limitation_paths": [],
                },
                ensure_ascii=False,
            )
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_production_shaped_input())

    assert draft.cited_paths == ("data.season.title", "data.date")
    result = guard_final_response(_production_shaped_input(), draft)
    assert result.accepted is False
    assert result.code == "cited_value_not_rendered"


def test_composer_fingerprint_includes_canonicalization_policy(monkeypatch) -> None:
    kwargs = {
        "send": AsyncMock(),
        "persona_projection": _persona_projection("간결하게"),
        "max_tokens": 1200,
        "backend_name": "fixture-backend",
    }
    original = FinalResponseComposer(**kwargs).fingerprint
    monkeypatch.setattr(
        "simpleclaw.agent.final_response_composer."
        "CITATION_CANONICALIZATION_POLICY_VERSION",
        "visible_subset_v2",
    )

    changed = FinalResponseComposer(**kwargs).fingerprint

    assert changed != original


def test_composer_fingerprint_includes_effective_temperature() -> None:
    kwargs = {
        "send": AsyncMock(),
        "persona_projection": _persona_projection("간결하게"),
        "max_tokens": 1200,
        "backend_name": "fixture-backend",
    }

    deterministic = FinalResponseComposer(**kwargs, temperature=0.0)
    configured = FinalResponseComposer(**kwargs, temperature=0.2)

    assert deterministic.temperature == 0.0
    assert configured.temperature == 0.2
    assert deterministic.fingerprint != configured.fingerprint


def test_composer_fingerprint_includes_projection_policy_and_content_hash() -> None:
    base = _persona_projection("간결하게")
    changed_policy = CompositionPersonaProjection(
        instruction_text=base.instruction_text,
        source_types=base.source_types,
        token_count=base.token_count,
        token_budget=base.token_budget,
        policy_version="fixture_v2",
        fingerprint="fixture-policy-v2",
    )

    first = FinalResponseComposer(
        send=AsyncMock(),
        persona_projection=base,
        max_tokens=1200,
        backend_name="fixture-backend",
    ).fingerprint
    second = FinalResponseComposer(
        send=AsyncMock(),
        persona_projection=changed_policy,
        max_tokens=1200,
        backend_name="fixture-backend",
    ).fingerprint

    assert first != second
