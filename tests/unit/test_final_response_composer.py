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
    CompositionRenderPlanV1,
    CompositionRenderSegmentV1,
    DraftResponseV1,
    StructuralEvidenceRelationV1,
)
from simpleclaw.agent.final_response_composer import (
    FinalResponseComposer,
    FinalResponseComposerError,
    materialize_render_plan,
)
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
        composition_list_root="data.items",
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


def _neutral_render_input() -> CompositionInputV1:
    evidence_paths = (
        "records[0].name",
        "records[0].state",
        "records[1].name",
        "records[1].state",
    )
    return CompositionInputV1(
        request_id="request-neutral-render",
        question="What are the top 2 records?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="neutral-render-payload-hash",
        composition_list_root="records",
        public_facts={
            "records": [
                {"name": "alpha", "state": "ready"},
                {"name": "beta", "state": "ready"},
            ]
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=evidence_paths,
                identity_paths=("records[0].name", "records[1].name"),
            ),
        ),
    )


def _neutral_render_plan(
    *,
    paths: tuple[str, ...] | None = None,
    connectors: tuple[str, ...] = (
        "space",
        "comma_space",
        "space",
        "period",
    ),
) -> CompositionRenderPlanV1:
    selected = (
        paths or _neutral_render_input().structural_evidence_relations[0].evidence_paths
    )
    return CompositionRenderPlanV1(
        segments=tuple(
            CompositionRenderSegmentV1(path=path, connector=connector)
            for path, connector in zip(selected, connectors, strict=True)
        )
    )


def _provider_plan_json(
    paths: list[str] | tuple[str, ...],
    *,
    connectors: list[str] | tuple[str, ...] | None = None,
    limitation_paths: list[str] | tuple[str, ...] = (),
) -> str:
    selected_connectors = connectors or tuple(
        "period" if index == len(paths) - 1 else "comma_space"
        for index in range(len(paths))
    )
    return json.dumps(
        {
            "schema": "composition_render_plan.v1",
            "segments": [
                {"path": path, "connector": connector}
                for path, connector in zip(
                    paths,
                    selected_connectors,
                    strict=True,
                )
            ],
            "limitation_paths": list(limitation_paths),
        },
        ensure_ascii=False,
    )


def test_composer_prompt_preserves_ordered_render_plan_contract() -> None:
    """Typed plan 외 final literal 작성 경로가 prompt에 없음을 고정한다."""
    prompt = load_system_prompt("langgraph_v4_composer", refresh=True)

    assert prompt.version == 12
    assert "CompositionRenderPlanV1" in prompt.system_prompt
    assert "bounded grammar enum" in prompt.system_prompt
    assert "central materializer" in prompt.system_prompt
    assert "identity_paths" in prompt.system_prompt
    assert "typed persona projection" in prompt.system_prompt
    assert "never content or" in prompt.system_prompt
    assert "provider" not in prompt.system_prompt.casefold()
    assert "recipe" not in prompt.system_prompt.casefold()
    assert "skill" not in prompt.system_prompt.casefold()
    assert "items[0]" not in prompt.system_prompt
    assert "A는 10" not in prompt.system_prompt


def test_draft_schema_requires_at_least_one_cited_path() -> None:
    schema = DraftResponseV1.model_json_schema(by_alias=True)

    assert "cited_paths" in schema["required"]
    assert schema["properties"]["cited_paths"]["minItems"] == 1
    assert schema["properties"]["cited_paths"]["maxItems"] == 128


@pytest.mark.asyncio
async def test_composer_schema_constrains_active_relation_to_exact_literal_plan() -> (
    None
):
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
            text=_provider_plan_json(("data.items[0].team", "data.items[1].team"))
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
    properties = schema["properties"]
    segment = schema["$defs"]["CompositionRenderSegmentV1"]["properties"]
    assert segment["path"]["enum"] == [
        "data.items[0].team",
        "data.items[1].team",
    ]
    assert properties["segments"]["minItems"] == 2
    assert properties["segments"]["maxItems"] == 2
    assert "content" not in properties


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
            text=_provider_plan_json(
                (
                    "data.items[0].team",
                    "data.items[1].team",
                    "data.items[2].team",
                )
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
    assert "persona is not factual" in request.system_prompt
    segment = request.response_schema["$defs"]["CompositionRenderSegmentV1"]
    assert segment["properties"]["path"]["enum"] == [
        "data.items[0].rank",
        "data.items[0].team",
        "data.items[1].rank",
        "data.items[1].team",
        "data.items[2].rank",
        "data.items[2].team",
    ]
    assert request.response_schema["properties"]["limitation_paths"]["maxItems"] == 0
    assert draft.content == "KT, 삼성, LG."


@pytest.mark.asyncio
async def test_composer_keeps_contract_with_production_persona_assembly(
    tmp_path,
) -> None:
    persona_dir = tmp_path / "persona"
    persona_dir.mkdir()
    (persona_dir / "SOUL.md").write_text(
        "# Identity\n\nSimpleClaw\n\n# Speaking Style\n\n따뜻하고 간결한 한국어 존댓말",
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
    content = "LG, 60, 한화, 58, 롯데, 55입니다."
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
            text=_provider_plan_json(
                paths,
                connectors=(
                    "comma_space",
                    "comma_space",
                    "comma_space",
                    "comma_space",
                    "comma_space",
                    "polite_copula_period",
                ),
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
    assert "bounded grammar" in request.system_prompt
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
            "composition_list_root": "alpha.nested",
        }
    )
    send = AsyncMock(
        return_value=LLMResponse(
            text=_provider_plan_json(
                ("alpha.nested[0].name",),
                connectors=("limitation_uncertain_period",),
                limitation_paths=(
                    "unresolved_claims[0]",
                    "unresolved_claims[1]",
                ),
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
    segment = schema["$defs"]["CompositionRenderSegmentV1"]["properties"]
    assert segment["path"]["enum"] == [
        "alpha.nested[0].active",
        "alpha.nested[0].name",
        "items[0].label",
        "items[0].value",
        "items[1].label",
    ]
    assert schema["properties"]["limitation_paths"]["items"]["enum"] == [
        "unresolved_claims[0]",
        "unresolved_claims[1]",
    ]
    encoded = json.dumps(schema, sort_keys=True)
    assert "public_facts." not in encoded
    assert "[*]" not in encoded
    assert "alpha.empty" not in encoded
    assert '"items"' not in segment["path"]["enum"]


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
async def test_composer_does_not_retry_empty_segments() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "segments": [],
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
    assert "segments" in request.response_schema["required"]
    assert request.response_schema["properties"]["segments"]["minItems"] == 1


@pytest.mark.asyncio
async def test_composer_rejects_reordered_paths_without_retry() -> None:
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
    send = AsyncMock(return_value=LLMResponse(text=_provider_plan_json(provider_paths)))
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    with pytest.raises(FinalResponseComposerError, match="canonical"):
        await composer.compose(_production_shaped_input())

    assert send.await_count == 1


@pytest.mark.asyncio
async def test_composer_never_adds_visible_citations_missing_from_provider() -> None:
    send = AsyncMock(
        return_value=LLMResponse(text=_provider_plan_json(("data.items[0].team",)))
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_production_shaped_input())

    assert draft.cited_paths == ("data.items[0].team",)
    assert draft.content == "LG."
    assert guard_final_response(_production_shaped_input(), draft).accepted is False


@pytest.mark.asyncio
async def test_composer_keeps_selected_citations_for_guard_scope_rejection() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=_provider_plan_json(("data.date", "data.season.title"))
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("간결하게"),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_production_shaped_input())

    assert draft.cited_paths == ("data.date", "data.season.title")
    result = guard_final_response(_production_shaped_input(), draft)
    assert result.accepted is False
    assert result.code == "requested_scope_not_fully_cited"


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


def test_render_plan_materializes_repeated_scalars_once_per_path() -> None:
    value = _neutral_render_input()

    draft = materialize_render_plan(value, _neutral_render_plan())

    assert draft.content == "alpha ready, beta ready."
    assert draft.cited_paths == value.structural_evidence_relations[0].evidence_paths
    assert draft.content.count("ready") == 2
    assert guard_final_response(value, draft).accepted is True


def test_render_plan_preserves_projected_case_exactly() -> None:
    value = _neutral_render_input().model_copy(
        update={
            "public_facts_json": (
                '{"records":[{"name":"Alpha","state":"READY"},'
                '{"name":"Beta","state":"READY"}]}'
            )
        }
    )

    draft = materialize_render_plan(value, _neutral_render_plan())

    assert draft.content == "Alpha READY, Beta READY."
    assert "alpha" not in draft.content
    assert "ready" not in draft.content


def test_render_plan_contract_forbids_provider_authored_content() -> None:
    with pytest.raises(ValidationError):
        CompositionRenderPlanV1.model_validate(
            {
                "segments": [
                    {
                        "path": "records[0].name",
                        "connector": "period",
                    }
                ],
                "content": "alpha on 2026-08-08 is rank 1.",
            }
        )


def test_render_plan_materializes_typed_limitation_ending() -> None:
    value = _neutral_render_input().model_copy(
        update={"unresolved_claims": ("missing detail",)}
    )
    plan = CompositionRenderPlanV1(
        segments=tuple(
            segment.model_copy(update={"connector": "limitation_uncertain_period"})
            if index == 3
            else segment
            for index, segment in enumerate(_neutral_render_plan().segments)
        ),
        limitation_paths=("unresolved_claims[0]",),
    )

    draft = materialize_render_plan(value, plan)

    assert draft.content == "alpha ready, beta ready uncertain."
    assert guard_final_response(value, draft).accepted is True


@pytest.mark.parametrize(
    "paths",
    [
        (
            "records[0].state",
            "records[0].name",
            "records[1].name",
            "records[1].state",
        ),
        (
            "records[0].name",
            "records[0].state",
            "records[0].state",
            "records[1].state",
        ),
        (
            "records[0].name",
            "records[0].state",
            "records[1].name",
        ),
        (
            "records[*].name",
            "records[0].state",
            "records[1].name",
            "records[1].state",
        ),
        (
            "records[0].name",
            "records[0].state",
            "records[1].name",
            "records[9].state",
        ),
    ],
    ids=("reordered", "duplicate", "missing", "wildcard", "invalid"),
)
def test_render_plan_rejects_noncanonical_relation_paths(
    paths: tuple[str, ...],
) -> None:
    connectors = tuple(
        "period" if index == len(paths) - 1 else "comma_space"
        for index in range(len(paths))
    )

    with pytest.raises(FinalResponseComposerError):
        materialize_render_plan(
            _neutral_render_input(),
            _neutral_render_plan(paths=paths, connectors=connectors),
        )


@pytest.mark.parametrize(
    "connector",
    (
        " detail ",
        " 3 ",
        " https://example.invalid ",
        " kg ",
        " before ",
    ),
)
def test_render_plan_rejects_free_form_connector_literals(connector: str) -> None:
    with pytest.raises(ValidationError):
        CompositionRenderPlanV1.model_validate(
            {
                "segments": [
                    {
                        "path": "records[0].name",
                        "connector": connector,
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    "facts",
    (
        {"records": [{"name": None}]},
        {"records": [{"name": " alpha "}]},
        {"records": [{"name": True}, {"name": 1}]},
        {"records": [{"name": "alpha"}], "auxiliary": [{"name": "beta"}]},
    ),
    ids=("null", "whitespace", "bool-number-collision", "auxiliary-list-root"),
)
def test_render_plan_rejects_unsafe_scalar_or_list_root(
    facts: dict[str, object],
) -> None:
    paths = tuple(
        path
        for path in (
            "records[0].name",
            "records[1].name",
            "auxiliary[0].name",
        )
        if (
            (path.startswith("records[0]") and facts["records"])
            or (path.startswith("records[1]") and len(facts["records"]) > 1)
            or path.startswith("auxiliary")
            and "auxiliary" in facts
        )
    )
    value = _neutral_render_input().model_copy(
        update={
            "public_facts_json": json.dumps(
                facts,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "structural_evidence_relations": (),
        }
    )
    plan = CompositionRenderPlanV1(
        segments=tuple(
            CompositionRenderSegmentV1(
                path=path,
                connector="period" if index == len(paths) - 1 else "comma_space",
            )
            for index, path in enumerate(paths)
        )
    )

    with pytest.raises(FinalResponseComposerError):
        materialize_render_plan(value, plan)


def test_render_plan_rejects_mixed_list_roots() -> None:
    paths = ("left[0].name", "right[0].name")
    value = _neutral_render_input().model_copy(
        update={
            "public_facts_json": '{"left":[{"name":"alpha"}],"right":[{"name":"beta"}]}',
            "composition_list_root": "left",
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=paths,
                    identity_paths=paths,
                ),
            ),
        }
    )
    plan = CompositionRenderPlanV1(
        segments=(
            CompositionRenderSegmentV1(
                path="left[0].name",
                connector="comma_space",
            ),
            CompositionRenderSegmentV1(
                path="right[0].name",
                connector="period",
            ),
        )
    )

    with pytest.raises(FinalResponseComposerError, match="mixes list roots"):
        materialize_render_plan(value, plan)


@pytest.mark.asyncio
async def test_composer_parses_plan_and_centrally_materializes_literals() -> None:
    send = AsyncMock(
        return_value=LLMResponse(
            text=_neutral_render_plan().model_dump_json(by_alias=True)
        )
    )
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("Use concise grammar."),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    draft = await composer.compose(_neutral_render_input())

    request = send.await_args.args[0]
    encoded_schema = json.dumps(request.response_schema, sort_keys=True)
    assert send.await_count == 1
    assert draft.content == "alpha ready, beta ready."
    assert "segments" in request.response_schema["properties"]
    assert "content" not in request.response_schema["properties"]
    assert '"content"' not in encoded_schema


@pytest.mark.asyncio
async def test_composer_does_not_retry_invalid_render_plan() -> None:
    invalid = json.dumps(
        {
            "segments": [
                {
                    "path": "records[0].name",
                    "connector": "rank 1 on 2026-08-08",
                }
            ],
            "limitation_paths": [],
        }
    )
    send = AsyncMock(return_value=LLMResponse(text=invalid))
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("Use concise grammar."),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    with pytest.raises(FinalResponseComposerError):
        await composer.compose(_neutral_render_input())

    assert send.await_count == 1


@pytest.mark.asyncio
async def test_composer_reraises_cancellation_from_single_provider_call() -> None:
    import asyncio

    send = AsyncMock(side_effect=asyncio.CancelledError)
    composer = FinalResponseComposer(
        send=send,
        persona_projection=_persona_projection("Use concise grammar."),
        max_tokens=1200,
        backend_name="fixture-backend",
    )

    with pytest.raises(asyncio.CancelledError):
        await composer.compose(_neutral_render_input())

    assert send.await_count == 1
