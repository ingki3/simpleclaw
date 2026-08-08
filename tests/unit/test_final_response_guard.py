"""BIZ-628 — 중앙 final response grounding guard."""

from __future__ import annotations

import json

import pytest

from simpleclaw.agent.composition_citations import canonicalize_draft_citations
from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    CompositionRenderPlanV1,
    DraftResponseV1,
    StructuralEvidenceRelationV1,
)
from simpleclaw.agent.final_response_composer import (
    FinalResponseComposerError,
    materialize_render_plan,
)
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus


def _input() -> CompositionInputV1:
    value = CompositionInputV1(
        request_id="request-1",
        question="현재 KBO 상위 3팀과 승수만 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        composition_list_root="data.items",
        public_facts={
            "data": {
                "category": "KBO",
                "items": [
                    {"rank": 1, "team": "KT", "wins": 59},
                    {"rank": 2, "team": "삼성", "wins": 58},
                    {"rank": 3, "team": "LG", "wins": 57},
                ]
            }
        },
    )
    root = value.composition_list_root
    assert root is not None
    projected: object = json.loads(value.public_facts_json)
    for segment in root.split("."):
        assert isinstance(projected, dict)
        projected = projected[segment]
    assert isinstance(projected, list) and projected
    first = projected[0]
    assert isinstance(first, dict)
    identity_fields = tuple(
        field for field, scalar in first.items() if isinstance(scalar, str)
    )
    measure_fields = tuple(
        field
        for field, scalar in first.items()
        if isinstance(scalar, (int, float)) and not isinstance(scalar, bool)
    )[-1:]
    evidence_paths = tuple(
        f"{root}[{index}].{field}"
        for index in range(len(projected))
        for field in (*identity_fields, *measure_fields)
    )
    identity_paths = tuple(
        f"{root}[{index}].{field}"
        for index in range(len(projected))
        for field in identity_fields
    )
    return value.model_copy(
        update={
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=evidence_paths,
                    identity_paths=identity_paths,
                ),
            )
        }
    )


def _neutral_empty_input(
    *,
    question: str = "Are any records available?",
) -> CompositionInputV1:
    return CompositionInputV1(
        request_id="request-neutral-empty",
        question=question,
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="empty-payload-hash",
        public_facts={
            "data": {
                "state": "absent",
                "reason": "none_available",
                "records": [],
            }
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("data.state",),
            ),
        ),
    )


def _neutral_records_input(
    *,
    question: str = "Return the first 3 records with their values.",
) -> CompositionInputV1:
    evidence_paths = tuple(
        f"records[{index}].{field}"
        for index in range(3)
        for field in ("name", "value")
    )
    return CompositionInputV1(
        request_id="request-neutral-records",
        question=question,
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="neutral-records-payload-hash",
        composition_list_root="records",
        public_facts={
            "records": [
                {"name": "alpha", "value": 59},
                {"name": "beta", "value": 58},
                {"name": "gamma", "value": 57},
            ]
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=evidence_paths,
                identity_paths=tuple(
                    f"records[{index}].name" for index in range(3)
                ),
            ),
        ),
    )


def _same_item_render_input(
    facts: dict[str, object],
    fields: tuple[str, ...],
) -> CompositionInputV1:
    evidence_paths = tuple(f"records[0].{field}" for field in fields)
    return CompositionInputV1(
        request_id="request-same-item-render",
        question="Return the record fields.",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="same-item-render-payload-hash",
        composition_list_root="records",
        public_facts={"records": [facts]},
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(evidence_paths=evidence_paths),
        ),
    )


def _non_structural_citable_input(
    *,
    citable_paths: tuple[str, ...] = ("record.first", "record.second"),
) -> CompositionInputV1:
    return CompositionInputV1(
        request_id="request-non-structural-resolved-claims",
        question="Return the resolved record fields.",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-record"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="non-structural-resolved-claims-payload-hash",
        public_facts={
            "record": {
                "first": "alpha",
                "second": "beta",
                "third": "gamma",
            }
        },
        resolved_claims=("aggregate",),
        citable_paths=citable_paths,
    )


def test_guard_accepts_exact_non_structural_citable_path_citations() -> None:
    value = _non_structural_citable_input()

    draft = materialize_render_plan(
        value,
        CompositionRenderPlanV1(separator="comma_space"),
    )

    assert draft.cited_paths == value.citable_paths
    assert draft.content == "alpha, beta."
    assert guard_final_response(value, draft).accepted is True


@pytest.mark.parametrize(
    "cited_paths",
    [
        ("record.first",),
        ("record.first", "record.second", "record.third"),
        ("record.second", "record.first"),
        ("record.first", "record.third"),
        ("record.First", "record.second"),
        (),
    ],
    ids=(
        "subset",
        "superset",
        "reordered",
        "wrong-path",
        "case-mismatch",
        "omitted",
    ),
)
def test_guard_rejects_non_structural_citable_path_citation_mismatch(
    cited_paths: tuple[str, ...],
) -> None:
    value = _non_structural_citable_input()
    draft = DraftResponseV1.model_construct(
        content="alpha, beta.",
        cited_paths=cited_paths,
        limitation_paths=(),
    )

    result = guard_final_response(value, draft)

    assert result.accepted is False
    assert result.code == "citable_path_citation_mismatch"


def test_guard_rejects_citations_when_non_structural_contract_is_empty() -> None:
    value = _non_structural_citable_input(citable_paths=())
    draft = DraftResponseV1(
        content="alpha.",
        cited_paths=("record.first",),
    )

    result = guard_final_response(value, draft)

    assert result.accepted is False
    assert result.code == "citable_path_citation_mismatch"


@pytest.mark.parametrize(
    ("facts", "fields", "expected_content"),
    [
        ({"left": 1, "right": 2}, ("left", "right"), "1, 2."),
        ({"flag": True, "state": "ready"}, ("flag", "state"), "true, ready."),
        ({"left": 1, "right": 1}, ("left", "right"), "1, 1."),
    ],
    ids=("number-number", "bool-string", "repeated-equal-number"),
)
def test_guard_accepts_exact_materialized_same_item_scalar_sequence(
    facts: dict[str, object],
    fields: tuple[str, ...],
    expected_content: str,
) -> None:
    value = _same_item_render_input(facts, fields)

    draft = materialize_render_plan(
        value,
        CompositionRenderPlanV1(separator="comma_space"),
    )

    assert draft.content == expected_content
    assert guard_final_response(value, draft).accepted is True


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        ("3, 2, 1.", "cited_value_order_mismatch"),
        ("1 and 2 and 3.", "cited_value_order_mismatch"),
        ("1, 2; 3.", "cited_value_order_mismatch"),
        ("1, 2, 3, hidden.", "cited_value_order_mismatch"),
        ("1, 2, 3, 4.", "cited_value_order_mismatch"),
        (" 1, 2, 3.", "ungrounded_symbol"),
        ("1, 2, 3..", "cited_value_order_mismatch"),
    ],
    ids=(
        "reversed-order",
        "semantic-connector",
        "mixed-separator",
        "uncited-scalar",
        "uncited-number",
        "leading-whitespace",
        "duplicate-punctuation",
    ),
)
def test_guard_rejects_noncanonical_same_item_scalar_sequence(
    content: str,
    expected_code: str,
) -> None:
    value = _same_item_render_input(
        {"left": 1, "middle": 2, "right": 3, "extra": "hidden"},
        ("left", "middle", "right"),
    )
    canonical = materialize_render_plan(
        value,
        CompositionRenderPlanV1(separator="comma_space"),
    )

    result = guard_final_response(
        value,
        canonical.model_copy(update={"content": content}),
    )

    assert result.code == expected_code


def test_materializer_rejects_same_item_typed_literal_collision() -> None:
    value = _same_item_render_input(
        {"string_value": "1", "number_value": 1},
        ("string_value", "number_value"),
    )

    with pytest.raises(FinalResponseComposerError, match="typed literal collision"):
        materialize_render_plan(
            value,
            CompositionRenderPlanV1(separator="comma_space"),
        )


@pytest.mark.parametrize(
    ("facts", "fields", "content"),
    [
        ({"flag": True, "number": 1}, ("flag", "number"), "true, 1."),
        ({"number": 1, "flag": True}, ("number", "flag"), "1, true."),
        ({"number": 1, "text": "1"}, ("number", "text"), "1, 1."),
        ({"flag": True, "text": "true"}, ("flag", "text"), "true, true."),
        ({"missing": None, "state": "ready"}, ("missing", "state"), "null, ready."),
    ],
    ids=(
        "bool-to-number",
        "number-to-bool",
        "number-to-same-literal-string",
        "bool-to-same-literal-string",
        "null-to-string",
    ),
)
def test_guard_rejects_exact_layout_materializer_inadmissible_sequence(
    facts: dict[str, object],
    fields: tuple[str, ...],
    content: str,
) -> None:
    value = _same_item_render_input(facts, fields)
    draft = DraftResponseV1(
        content=content,
        cited_paths=tuple(f"records[0].{field}" for field in fields),
    )

    with pytest.raises(FinalResponseComposerError):
        materialize_render_plan(
            value,
            CompositionRenderPlanV1(separator="comma_space"),
        )
    result = guard_final_response(value, draft)

    assert result.accepted is False
    assert result.code == "cited_value_order_mismatch"


def test_guard_accepts_visible_exact_structural_evidence() -> None:
    result = guard_final_response(
        _neutral_empty_input(),
        DraftResponseV1(
            content="absent.",
            cited_paths=("data.state",),
        ),
    )

    assert result.accepted is True


def test_guard_rejects_undeclared_structural_relation_cause() -> None:
    result = guard_final_response(
        _neutral_empty_input(),
        DraftResponseV1(
            content="No records are available because maintenance.",
            cited_paths=("data.state",),
        ),
    )

    assert result.accepted is False


@pytest.mark.parametrize(
    "content",
    [
        "ready unavailable.",
        "ready is absent.",
        "No records maintenance unavailable.",
        "No records hacked unavailable.",
        "No records are unavailable tomorrow.",
        "No records corrupted unavailable.",
        "No records unicorn available.",
        "No records fabricated outage available.",
    ],
)
def test_guard_rejects_undeclared_absence_relation_tokens(content: str) -> None:
    value = _neutral_empty_input()
    if content.startswith("ready"):
        value = value.model_copy(
            update={'public_facts_json': '{"data":{"state":"ready"}}'}
        )
    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=("data.state",)),
    )

    assert result.accepted is False


@pytest.mark.parametrize(
    "content",
    [
        "No records are currently unavailable.",
        "records absent.",
    ],
)
def test_guard_rejects_implicit_or_question_derived_relation(content: str) -> None:
    result = guard_final_response(
        _neutral_empty_input(),
        DraftResponseV1(content=content, cited_paths=("data.state",)),
    )

    assert result.accepted is False


def test_canonicalizer_preserves_undeclared_relation_citation_for_rejection() -> None:
    draft = DraftResponseV1(
        content="absent.",
        cited_paths=("data.state", "data.reason"),
    )

    canonical = canonicalize_draft_citations(_neutral_empty_input(), draft)

    assert canonical is draft
    assert guard_final_response(_neutral_empty_input(), canonical).code == (
        "structural_relation_citation_mismatch"
    )


def test_guard_accepts_declared_question_scoped_state() -> None:
    value = CompositionInputV1(
        request_id="request-neutral-state",
        question="What is the record state?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="state-payload-hash",
        public_facts={"data": {"state": "ready"}},
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("data.state",),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="ready.",
            cited_paths=("data.state",),
        ),
    )

    assert result.accepted is True


@pytest.mark.parametrize(
    "content",
    [
        "ready is.",
        "ready with.",
        "ready respectively.",
        "ready 입니다.",
    ],
)
def test_guard_rejects_semantic_or_localized_residual_connector(
    content: str,
) -> None:
    value = _neutral_empty_input().model_copy(
        update={'public_facts_json': '{"data":{"state":"ready"}}'}
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=("data.state",)),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_question_force_after_single_scalar() -> None:
    value = _neutral_empty_input().model_copy(
        update={'public_facts_json': '{"data":{"state":"ready"}}'}
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content="ready?", cited_paths=("data.state",)),
    )

    assert result.code == "ungrounded_symbol"


@pytest.mark.parametrize(
    "content",
    [
        "ready waiting.",
        "ready, waiting.",
        "ready · waiting.",
        "ready; waiting.",
    ],
)
def test_guard_accepts_exact_materializer_structural_separators(
    content: str,
) -> None:
    value = _neutral_empty_input().model_copy(
        update={
            'public_facts_json': '{"data":{"state":"ready","phase":"waiting"}}',
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=("data.state", "data.phase"),
                ),
            ),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=("data.state", "data.phase"),
        ),
    )

    assert result.accepted is True


@pytest.mark.parametrize(
    "content",
    [
        "ready, waiting; paused.",
        "ready, waiting, paused..",
        " ready, waiting, paused.",
        "ready, waiting, paused. ",
    ],
)
def test_guard_rejects_non_materializer_punctuation_sequence(content: str) -> None:
    value = _neutral_empty_input().model_copy(
        update={
            'public_facts_json': (
                '{"data":{"state":"ready","phase":"waiting",'
                '"mode":"paused"}}'
            ),
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=("data.state", "data.phase", "data.mode"),
                ),
            ),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=("data.state", "data.phase", "data.mode"),
        ),
    )

    assert result.code == "ungrounded_symbol"


@pytest.mark.parametrize("content", ["READY.", "Ready."])
def test_guard_rejects_case_changed_opaque_string_evidence(content: str) -> None:
    value = CompositionInputV1(
        request_id="request-neutral-state-case-change",
        question="What is the record state?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="state-case-change-payload-hash",
        public_facts={"data": {"state": "ready"}},
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("data.state",),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=("data.state",)),
    )

    assert result.accepted is False
    assert result.code == "cited_value_not_rendered"


@pytest.mark.parametrize("evidence", [" ready ", "ready ", " ready"])
@pytest.mark.parametrize("content", ["ready.", " ready ."])
def test_guard_rejects_edge_whitespace_string_evidence(
    evidence: str,
    content: str,
) -> None:
    value = CompositionInputV1(
        request_id="request-neutral-state-edge-whitespace",
        question="What is the record state?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="state-edge-whitespace-payload-hash",
        public_facts={"data": {"state": evidence}},
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("data.state",),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=("data.state",)),
    )

    assert result.accepted is False
    assert result.code == "citation_not_scalar"


@pytest.mark.parametrize(
    "content",
    [
        "The record state is ready unicorn.",
        "Fabricated outage says ready.",
        "ready hacked tomorrow.",
        "ready confidential internal.",
        "ready corrupted.",
    ],
)
def test_guard_rejects_undeclared_state_relation_tokens(content: str) -> None:
    value = CompositionInputV1(
        request_id="request-neutral-state-mutation",
        question="What is the record state?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="state-mutation-payload-hash",
        public_facts={"data": {"state": "ready"}},
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("data.state",),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=("data.state",)),
    )

    assert result.accepted is False


def test_structural_relation_cannot_bypass_top_n_cardinality() -> None:
    value = CompositionInputV1(
        request_id="request-neutral-top-two",
        question="What are the top 2 record states?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="top-two-payload-hash",
        composition_list_root="records",
        public_facts={
            "records": [{"state": "alpha"}, {"state": "beta"}]
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("records[0].state",),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="The record state is alpha.",
            cited_paths=("records[0].state",),
        ),
    )

    assert result.code == "requested_scope_not_fully_cited"


def _neutral_top_two_relation(
    *,
    include_identity: bool = True,
    repeated_state: bool = False,
) -> CompositionInputV1:
    second_state = "ready" if repeated_state else "waiting"
    evidence_paths = (
        "records[0].name",
        "records[0].state",
        "records[1].name",
        "records[1].state",
    )
    if not include_identity:
        evidence_paths = ("records[0].state", "records[1].state")
    return CompositionInputV1(
        request_id="request-neutral-top-two-relation",
        question="What are the top 2 records?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="top-two-relation-payload-hash",
        composition_list_root="records",
        public_facts={
            "records": [
                {"name": "alpha", "state": "ready"},
                {"name": "beta", "state": second_state},
            ]
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=evidence_paths,
                identity_paths=(
                    ("records[0].name", "records[1].name")
                    if include_identity
                    else ()
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    "content",
    [
        "alpha is beta.",
        "No record states are available.",
        "record beta, alpha.",
    ],
)
def test_structural_relation_rejects_shape_implicit_and_reversed_content(
    content: str,
) -> None:
    value = _neutral_top_two_relation()
    if content != "No record states are available.":
        value = value.model_copy(
            update={
                "structural_evidence_relations": (
                    StructuralEvidenceRelationV1(
                        evidence_paths=(
                            "records[0].name",
                            "records[1].name",
                        ),
                        identity_paths=(
                            "records[0].name",
                            "records[1].name",
                        ),
                    ),
                )
            }
        )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.accepted is False


def test_structural_relation_rejects_semantic_korean_suffix() -> None:
    value = _neutral_empty_input(question="알파 상태?").model_copy(
        update={'public_facts_json': '{"data":{"state":"ready"}}'}
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content="알파보다 ready 입니다.", cited_paths=("data.state",)),
    )

    assert result.accepted is False


def test_guard_rejects_ungrounded_semantic_predicate_suffix() -> None:
    value = _neutral_empty_input(question="현재 상태를 알려줘").model_copy(
        update={'public_facts_json': '{"data":{"state":"ready"}}'}
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content="ready됩니다.", cited_paths=("data.state",)),
    )

    assert result.code == "ungrounded_text"


def test_structural_relation_requires_declared_top_n_identity() -> None:
    value = _neutral_top_two_relation(
        include_identity=False,
        repeated_state=True,
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="ready, ready.",
            cited_paths=("records[0].state", "records[1].state"),
        ),
    )

    assert result.code == "requested_item_identity_not_cited"


def test_structural_relation_does_not_infer_alternate_unique_string_identity(
) -> None:
    value = _neutral_top_two_relation(include_identity=False)

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="ready, waiting.",
            cited_paths=("records[0].state", "records[1].state"),
        ),
    )

    assert result.code == "requested_item_identity_not_cited"


def test_structural_relation_accepts_visible_identity_evidence_in_source_order() -> None:
    value = _neutral_top_two_relation()

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, ready, beta, waiting.",
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.accepted is True


def test_structural_relation_accepts_repeated_scalar_materialized_per_identity() -> None:
    value = _neutral_top_two_relation(repeated_state=True)

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, ready, beta, ready.",
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.accepted is True


def test_structural_relation_accepts_repeated_number_before_terminal_punctuation(
) -> None:
    value = CompositionInputV1(
        request_id="request-neutral-repeated-number",
        question="What are the top 3 records and values?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="repeated-number-payload-hash",
        composition_list_root="records",
        public_facts={
            "records": [
                {"name": "alpha", "value": 60},
                {"name": "beta", "value": 55},
                {"name": "gamma", "value": 55},
            ]
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=tuple(
                    f"records[{index}].{field}"
                    for index in range(3)
                    for field in ("name", "value")
                ),
                identity_paths=tuple(
                    f"records[{index}].name" for index in range(3)
                ),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 60, beta, 55, gamma, 55.",
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.accepted is True


def test_structural_relation_rejects_top_n_mixed_list_roots() -> None:
    value = CompositionInputV1(
        request_id="request-neutral-mixed-roots",
        question="What are the top 2 records?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="mixed-roots-payload-hash",
        composition_list_root="left",
        public_facts={
            "left": [{"name": "alpha"}],
            "right": [{"name": "unused"}, {"name": "beta"}],
        },
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("left[0].name", "right[1].name"),
                identity_paths=("left[0].name", "right[1].name"),
            ),
        ),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, beta.",
            cited_paths=("left[0].name", "right[1].name"),
        ),
    )

    assert result.code == "requested_scope_mixed_list_roots"


def test_top_n_rejects_list_root_not_declared_by_descriptor() -> None:
    value = _neutral_top_two_relation().model_copy(
        update={"composition_list_root": "other_records"}
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha ready, beta waiting.",
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.code == "requested_scope_list_root_not_declared"


def test_top_n_rejects_auxiliary_declared_wildcard_root() -> None:
    value = CompositionInputV1(
        request_id="request-neutral-auxiliary-root",
        question="What are the top 2 records?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="auxiliary-root-payload-hash",
        composition_list_root="records",
        public_facts={
            "records": [{"name": "real-a"}, {"name": "real-b"}],
            "warnings": ["alpha", "beta"],
        },
        citable_paths=("warnings[0]", "warnings[1]"),
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="top 2 records alpha, beta.",
            cited_paths=("warnings[0]", "warnings[1]"),
        ),
    )

    assert result.code == "requested_scope_list_root_not_declared"


def test_non_top_n_guard_and_materializer_reject_mixed_list_roots() -> None:
    value = CompositionInputV1(
        request_id="request-neutral-non-top-mixed",
        question="Return the projected records.",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="non-top-mixed-hash",
        composition_list_root="left",
        public_facts={
            "left": [{"name": "alpha"}],
            "right": [{"name": "beta"}],
        },
        citable_paths=("left[0].name", "right[0].name"),
    )
    draft = DraftResponseV1(
        content="alpha, beta.",
        cited_paths=value.citable_paths,
    )

    guarded = guard_final_response(value, draft)
    with pytest.raises(FinalResponseComposerError, match="mixes list roots"):
        materialize_render_plan(value, CompositionRenderPlanV1(separator="comma_space"))

    assert guarded.code == "citation_mixed_list_roots"


def test_non_top_n_guard_and_materializer_reject_auxiliary_list_root() -> None:
    value = CompositionInputV1(
        request_id="request-neutral-non-top-auxiliary",
        question="Return the projected records.",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="non-top-auxiliary-hash",
        composition_list_root="left",
        public_facts={
            "left": [{"name": "unused"}],
            "right": [{"name": "alpha"}],
        },
        citable_paths=("right[0].name",),
    )
    draft = DraftResponseV1(content="alpha.", cited_paths=value.citable_paths)

    guarded = guard_final_response(value, draft)
    with pytest.raises(FinalResponseComposerError, match="auxiliary list root"):
        materialize_render_plan(value, CompositionRenderPlanV1(separator="space"))

    assert guarded.code == "citation_auxiliary_list_root"


def test_structural_relation_rejects_required_evidence_subset() -> None:
    value = _neutral_top_two_relation()
    subset = DraftResponseV1(
        content="alpha.",
        cited_paths=("records[0].name",),
    )

    result = guard_final_response(value, subset)

    assert result.code == "structural_relation_citation_mismatch"


def test_canonicalizer_preserves_full_required_set_when_literals_are_missing() -> None:
    value = _neutral_top_two_relation()
    provider_draft = DraftResponseV1(
        content="alpha.",
        cited_paths=value.structural_evidence_relations[0].evidence_paths,
    )

    canonical = canonicalize_draft_citations(value, provider_draft)

    assert canonical is provider_draft
    assert guard_final_response(value, canonical).code == "cited_value_not_rendered"


def test_relation_canonicalizer_preserves_malformed_provider_citation() -> None:
    draft = DraftResponseV1(
        content="No records are available.",
        cited_paths=("data.state", "bogus.path"),
    )

    canonical = canonicalize_draft_citations(_neutral_empty_input(), draft)

    assert canonical is draft
    assert guard_final_response(_neutral_empty_input(), canonical).code == (
        "citation_not_projected"
    )


def test_visible_boolean_number_and_null_citations_are_preserved_then_rejected() -> None:
    value = _neutral_empty_input().model_copy(
        update={
            "question": "What are the three values?",
            "public_facts_json": '{"flag":true,"count":2,"missing":null}',
            "citable_paths": ("flag", "count", "missing"),
            "structural_evidence_relations": (),
        }
    )
    draft = DraftResponseV1(
        content="true, 2, null.",
        cited_paths=("flag", "count", "missing"),
    )

    canonical = canonicalize_draft_citations(value, draft)

    assert canonical is draft
    result = guard_final_response(value, canonical)
    assert result.accepted is False
    assert result.code == "ungrounded_symbol"


def test_guard_uses_type_strict_literal_ownership_for_bool_and_number() -> None:
    value = _neutral_empty_input().model_copy(
        update={
            "question": "What is the value?",
            "public_facts_json": '{"flag":true,"number":1}',
            "citable_paths": ("number",),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content="1 true.", cited_paths=("number",)),
    )

    assert result.code == "rendered_value_not_cited"


@pytest.mark.parametrize(
    ("public_facts_json", "content", "expected_code"),
    [
        ('{"label":"alpha","value":true}', "alpha, true.", "rendered_value_not_cited"),
        ('{"label":"alpha","value":null}', "alpha, null.", "rendered_value_not_cited"),
        ('{"label":"alpha","value":2}', "alpha, 2.", "ungrounded_number"),
    ],
)
def test_guard_rejects_visible_uncited_scalar_for_every_json_scalar_type(
    public_facts_json: str,
    content: str,
    expected_code: str,
) -> None:
    value = _neutral_empty_input().model_copy(
        update={
            "question": "What is alpha?",
            "public_facts_json": public_facts_json,
            "citable_paths": ("label",),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=("label",)),
    )

    assert result.code == expected_code


def test_guard_accepts_grounded_materializer_response() -> None:
    value = _neutral_records_input()
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57.",
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.accepted is True


def test_guard_rejects_semantic_residual_after_projected_fields() -> None:
    value = _neutral_records_input()
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57 respectively.",
            cited_paths=value.structural_evidence_relations[0].evidence_paths,
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_question_grounded_units_in_generic_list() -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KT는 59승, 삼성은 58승, LG는 57승입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[0].wins",
                "data.items[1].team",
                "data.items[1].wins",
                "data.items[2].team",
                "data.items[2].wins",
            ),
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_allows_requested_top_n_only_inside_scope_phrase() -> None:
    value = _neutral_records_input()
    cited_paths = value.structural_evidence_relations[0].evidence_paths
    accepted = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57.",
            cited_paths=cited_paths,
        ),
    )
    rejected = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57, 3.",
            cited_paths=cited_paths,
        ),
    )

    assert accepted.accepted is True
    assert rejected.code == "ungrounded_number"


def test_guard_requires_exact_requested_top_n_classifier() -> None:
    value = _neutral_records_input()
    cited_paths = value.structural_evidence_relations[0].evidence_paths

    accepted = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57.",
            cited_paths=cited_paths,
        ),
    )
    rejected = guard_final_response(
        value,
        DraftResponseV1(
            content="first 3 values: alpha, 59, beta, 58, gamma, 57.",
            cited_paths=cited_paths,
        ),
    )

    assert accepted.accepted is True
    assert rejected.accepted is False
    assert rejected.code == "ungrounded_text"


@pytest.mark.parametrize(
    ("question", "content"),
    [
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3결과는 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3순서는 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3현재는 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3팀보다 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3팀처럼 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3팀의 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3팀은 KT는 59, 삼성은 58, LG는 57이며 상위 3결과입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3팀은 KT는 59, 삼성은 58, LG는 57이며 상위 3팀입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "KT는 59, 삼성은 58, LG는 57이며 상위 3팀입니다.",
        ),
        (
            "결과와 현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3결과는 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3팀team은 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
        (
            "현재 KBO 순위 상위 3팀을 승수와 함께 알려줘",
            "상위 3clubs는 KT는 59, 삼성은 58, LG는 57입니다.",
        ),
    ],
)
def test_guard_rejects_invalid_top_n_classifier_slot(
    question: str,
    content: str,
) -> None:
    value = _input().model_copy(update={"question": question})

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=(
                "data.items[0].team",
                "data.items[0].wins",
                "data.items[1].team",
                "data.items[1].wins",
                "data.items[2].team",
                "data.items[2].wins",
            ),
        ),
    )

    assert result.accepted is False
    assert result.code == "ungrounded_text"


@pytest.mark.parametrize(
    "content",
    [
        "KT는 59, 삼성은 58, LG는 57이며 3입니다.",
        "KT의 59는 삼성의 58입니다. LG는 57입니다.",
    ],
)
def test_guard_rejects_security_amendment_exact_regressions(content: str) -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content=content,
            cited_paths=(
                "data.items[0].team",
                "data.items[0].wins",
                "data.items[1].team",
                "data.items[1].wins",
                "data.items[2].team",
                "data.items[2].wins",
            ),
        ),
    )

    assert result.code in {
        "cited_value_order_mismatch",
        "ungrounded_number",
        "ungrounded_text",
    }


@pytest.mark.parametrize(
    ("public_facts_json", "cited_paths"),
    [
        (
            '{"records":[{"label":"A","value":3},{"label":"B","value":2}]}',
            (
                "records[0].label",
                "records[0].value",
                "records[1].label",
                "records[1].value",
            ),
        ),
        (
            '{"entries":[{"name":"A","count":3},{"name":"B","count":2}]}',
            (
                "entries[0].name",
                "entries[0].count",
                "entries[1].name",
                "entries[1].count",
            ),
        ),
    ],
)
def test_guard_rejects_cross_item_relations_for_domain_neutral_fields(
    public_facts_json: str,
    cited_paths: tuple[str, ...],
) -> None:
    value = _input().model_copy(
        update={
            "question": "두 항목의 개수를 알려줘",
            "composition_list_root": cited_paths[0].split("[", 1)[0],
            "public_facts_json": public_facts_json,
            "citable_paths": cited_paths,
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="A의 3은 B의 2입니다.",
            cited_paths=cited_paths,
        ),
    )

    assert result.code in {
        "cited_value_not_rendered",
        "cited_value_order_mismatch",
    }


@pytest.mark.parametrize(
    ("public_facts_json", "cited_paths"),
    [
        (
            (
                '{"first_label":"A","first_value":3,'
                '"second_label":"B","second_value":2}'
            ),
            ("first_label", "first_value", "second_label", "second_value"),
        ),
        (
            (
                '{"metrics":{"first_label":"A","first_value":3,'
                '"second_label":"B","second_value":2}}'
            ),
            (
                "metrics.first_label",
                "metrics.first_value",
                "metrics.second_label",
                "metrics.second_value",
            ),
        ),
    ],
)
def test_guard_rejects_relation_reassembly_without_list_locations(
    public_facts_json: str,
    cited_paths: tuple[str, ...],
) -> None:
    value = _input().model_copy(
        update={
            "question": "두 지표 값을 알려줘",
            "public_facts_json": public_facts_json,
            "citable_paths": cited_paths,
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="A의 3은 B의 2입니다.",
            cited_paths=cited_paths,
        ),
    )

    assert result.code == "cited_value_order_mismatch"


def test_guard_accepts_root_scalar_label_value_sequence() -> None:
    value = _input().model_copy(
        update={
            "question": "두 지표 값을 알려줘",
            "public_facts_json": (
                '{"first_label":"A","first_value":3,'
                '"second_label":"B","second_value":2}'
            ),
            "citable_paths": (
                "first_label",
                "first_value",
                "second_label",
                "second_value",
            ),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="A, 3, B, 2.",
            cited_paths=(
                "first_label",
                "first_value",
                "second_label",
                "second_value",
            ),
        ),
    )

    assert result.accepted is True


def test_guard_rejects_cross_container_numeric_predicate_reassembly() -> None:
    value = _input().model_copy(
        update={
            "question": "두 지표 값을 알려줘",
            "public_facts_json": (
                '{"left":[{"label":"A","value":3}],'
                '"right":[{"label":"B","value":2}]}'
            ),
            "citable_paths": (
                "left[0].label",
                "left[0].value",
                "right[0].label",
                "right[0].value",
            ),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="A는 3은 B는 2입니다.",
            cited_paths=(
                "left[0].label",
                "left[0].value",
                "right[0].label",
                "right[0].value",
            ),
        ),
    )

    assert result.code == "citation_mixed_list_roots"


def test_guard_accepts_domain_neutral_label_value_materializer_sequence() -> None:
    value = _input().model_copy(
        update={
            "question": "두 항목의 개수를 알려줘",
            "composition_list_root": "records",
            "public_facts_json": (
                '{"records":['
                '{"label":"A","value":3},'
                '{"label":"B","value":2}'
                "]}"
            ),
            "citable_paths": (
                "records[0].label",
                "records[0].value",
                "records[1].label",
                "records[1].value",
            ),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="A, 3, B, 2.",
            cited_paths=(
                "records[0].label",
                "records[0].value",
                "records[1].label",
                "records[1].value",
            ),
        ),
    )

    assert result.accepted is True


@pytest.mark.parametrize(
    "content",
    [
        "A는 3입니다. B는 2입니다.",
        "A는 3개, B는 2건입니다.",
        "A는 3kg, B는 2kg입니다.",
    ],
)
def test_guard_rejects_cross_item_predicate_or_ungrounded_units(
    content: str,
) -> None:
    value = _input().model_copy(
        update={
            "question": "두 항목의 개수를 알려줘",
            "composition_list_root": "records",
            "public_facts_json": (
                '{"records":['
                '{"label":"A","value":3},'
                '{"label":"B","value":2}'
                "]}"
            ),
            "citable_paths": (
                "records[0].label",
                "records[0].value",
                "records[1].label",
                "records[1].value",
            ),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=(
                "records[0].label",
                "records[0].value",
                "records[1].label",
                "records[1].value",
            ),
        ),
    )

    assert result.code in {"cited_value_order_mismatch", "ungrounded_text"}


def test_guard_rejects_reversed_value_to_label_relation_within_item() -> None:
    value = _input().model_copy(
        update={
            "question": "항목의 개수를 알려줘",
            "composition_list_root": "items",
            "public_facts_json": '{"items":[{"value":3,"label":"A"}]}',
            "citable_paths": ("items[0].value", "items[0].label"),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="3은 A입니다.",
            cited_paths=("items[0].value", "items[0].label"),
        ),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize(
    "content",
    [
        "KT는 59승, 삼성은 58, LG는 57입니다.",
        "KT는 59 우승, 삼성은 58, LG는 57입니다.",
    ],
)
def test_guard_rejects_unprojected_unit_or_domain_term(content: str) -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content=content,
            cited_paths=(
                "data.items[0].team",
                "data.items[0].wins",
                "data.items[1].team",
                "data.items[1].wins",
                "data.items[2].team",
                "data.items[2].wins",
            ),
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_defensively_rejects_empty_citations() -> None:
    draft = DraftResponseV1.model_construct(
        content="KT입니다.",
        cited_paths=(),
        limitation_paths=(),
    )

    result = guard_final_response(_input(), draft)

    assert result.code == "citations_required"


def test_guard_accepts_128_citations_at_contract_boundary() -> None:
    facts = {f"measure_{index:03d}": f"value_{index:03d}" for index in range(128)}
    paths = tuple(facts)
    value = CompositionInputV1(
        request_id="request-128-citations",
        question="Return the projected measures.",
        locale="en-US",
        selected_route="react",
        asset_ref=AssetRefV1(type="skill", name="neutral-measures"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="citation-boundary-hash",
        public_facts=facts,
        citable_paths=paths,
    )
    draft = materialize_render_plan(
        value,
        CompositionRenderPlanV1(separator="comma_space"),
    )

    result = guard_final_response(value, draft)

    assert len(draft.cited_paths) == 128
    assert result.accepted is True


def test_guard_rejects_129_citations_constructed_without_validation() -> None:
    value = _neutral_records_input(question="Return alpha.").model_copy(
        update={
            "citable_paths": ("records[0].name",),
            "structural_evidence_relations": (),
        }
    )
    draft = DraftResponseV1.model_construct(
        content="alpha.",
        cited_paths=tuple(f"measure_{index:03d}" for index in range(129)),
        limitation_paths=(),
    )

    result = guard_final_response(value, draft)

    assert result.code == "invalid_draft_contract"


@pytest.mark.parametrize(
    "draft",
    [
        object(),
        DraftResponseV1.model_construct(
            cited_paths=("records[0].name",), limitation_paths=()
        ),
        DraftResponseV1.model_construct(
            content=123,
            cited_paths=("records[0].name",),
            limitation_paths=(),
        ),
        DraftResponseV1.model_construct(
            content="alpha.",
            cited_paths=["records[0].name"],
            limitation_paths=(),
        ),
    ],
)
def test_guard_rejects_wrong_type_or_shape_draft_contract(draft: object) -> None:
    result = guard_final_response(_neutral_records_input(), draft)  # type: ignore[arg-type]

    assert result.code == "invalid_draft_contract"


@pytest.mark.parametrize(
    ("cited_paths", "expected_code"),
    [
        (("data.items[*].team",), "citation_not_projected"),
        (("data.items[99].team",), "citation_not_projected"),
        (("public_facts.data.items[0].team",), "citation_not_projected"),
        (
            ("data.items[0].team", "data.items[0].team"),
            "duplicate_citation",
        ),
    ],
)
def test_guard_rejects_wildcard_unknown_and_duplicate_citations(
    cited_paths: tuple[str, ...],
    expected_code: str,
) -> None:
    draft = DraftResponseV1.model_construct(
        content="KT입니다.",
        cited_paths=cited_paths,
        limitation_paths=(),
    )

    result = guard_final_response(_input(), draft)

    assert result.code == expected_code


@pytest.mark.parametrize(
    ("cited_paths", "expected_code"),
    [
        (("data.items[*].team",), "citation_not_projected"),
        (("data.items[99].team",), "citation_not_projected"),
        (
            ("data.items[0].team", "data.items[0].team"),
            "duplicate_citation",
        ),
    ],
)
def test_canonicalizer_does_not_hide_invalid_citations(
    cited_paths: tuple[str, ...],
    expected_code: str,
) -> None:
    draft = DraftResponseV1.model_construct(
        content="KT입니다.",
        cited_paths=cited_paths,
        limitation_paths=(),
    )

    canonical = canonicalize_draft_citations(_input(), draft)

    assert canonical is draft
    assert guard_final_response(_input(), canonical).code == expected_code


def test_canonicalizer_does_not_prune_out_of_scope_citation() -> None:
    value = _input().model_copy(update={"question": "현재 상위 2팀"})
    draft = DraftResponseV1(
        content="KT, 삼성.",
        cited_paths=(
            "data.items[0].team",
            "data.items[1].team",
            "data.items[2].team",
        ),
    )

    canonical = canonicalize_draft_citations(value, draft)

    assert canonical is draft
    assert guard_final_response(value, canonical).code == (
        "citation_outside_requested_scope"
    )


def test_guard_rejects_unseen_fact_path_and_raw_contract_text() -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content="asset_result.v1 기준 두산이 1위입니다.",
            cited_paths=("data.items[99].team",),
        ),
    )

    assert result.accepted is False
    assert result.code == "raw_contract_exposed"


def test_guard_rejects_ungrounded_number_and_scope_overrun() -> None:
    value = _neutral_records_input(question="What is alpha's value?")
    number = guard_final_response(
        value.model_copy(
            update={
                "citable_paths": ("records[0].name",),
                "structural_evidence_relations": (),
            }
        ),
        DraftResponseV1(
            content="alpha is 99.",
            cited_paths=("records[0].name",),
        ),
    )
    scope = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KT, 삼성, LG 외 팀입니다.",
            cited_paths=("data.items[3].team",),
        ),
    )

    assert number.code == "ungrounded_number"
    assert scope.code == "citation_not_projected"


def test_guard_rejects_partial_top_n_uncited_fact_and_private_identifier() -> None:
    partial = guard_final_response(
        _input(),
        DraftResponseV1(
            content="현재 상위 3팀 중 KT입니다.",
            cited_paths=("data.items[0].team",),
        ),
    )
    uncited = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KT, 삼성, LG이며 58입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
        ),
    )
    private = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KT, 삼성, LG입니다. 주민번호는 ABCDEF입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
        ),
    )

    assert partial.code in {
        "requested_scope_not_fully_cited",
        "structural_relation_citation_mismatch",
    }
    assert uncited.code in {
        "ungrounded_number",
        "structural_relation_citation_mismatch",
    }
    assert private.code == "raw_contract_exposed"


def test_persona_conflict_cannot_bypass_grounding_citation_top_n_or_effect() -> None:
    """Persona는 guard 입력 자체가 아니므로 충돌 지시에도 이 판정은 불변이다."""
    unknown_citation = guard_final_response(
        _input(),
        DraftResponseV1(
            content="두산입니다.", cited_paths=("data.items[99].team",)
        ),
    )
    ungrounded = guard_final_response(
        _neutral_records_input(question="Return the first record.").model_copy(
            update={
                "structural_evidence_relations": (
                    StructuralEvidenceRelationV1(
                        evidence_paths=("records[0].name",),
                        identity_paths=("records[0].name",),
                    ),
                )
            }
        ),
        DraftResponseV1(
            content="alpha is certainly preferred.",
            cited_paths=("records[0].name",),
        ),
    )
    partial_top_n = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KT입니다.", cited_paths=("data.items[0].team",)
        ),
    )
    unsafe_effect = guard_final_response(
        _input().model_copy(update={"effect_status": EffectStatus.AUTHORIZED}),
        DraftResponseV1(
            content="KT, 삼성, LG입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
        ),
    )

    assert unknown_citation.code == "citation_not_projected"
    assert ungrounded.code == "ungrounded_text"
    assert partial_top_n.code in {
        "requested_scope_not_fully_cited",
        "structural_relation_citation_mismatch",
    }
    assert unsafe_effect.code == "unsafe_result"


def test_guard_requires_visible_limitation_for_every_unresolved_claim() -> None:
    value = _neutral_records_input(question="List the records.").model_copy(
        update={
            "citable_paths": (
                "records[0].name",
                "records[1].name",
                "records[2].name",
            ),
            "unresolved_claims": ("missing detail",),
            "structural_evidence_relations": (),
        }
    )
    certain = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, beta, gamma.",
            cited_paths=(
                "records[0].name",
                "records[1].name",
                "records[2].name",
            ),
            limitation_paths=("unresolved_claims[0]",),
        ),
    )

    assert certain.code == "limitation_not_rendered"


def test_guard_rejects_semantic_limitation_language() -> None:
    value = _neutral_records_input(question="List the records.").model_copy(
        update={
            "citable_paths": (
                "records[0].name",
                "records[1].name",
                "records[2].name",
            ),
            "unresolved_claims": ("missing detail",),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, beta, gamma. Cannot verify.",
            cited_paths=(
                "records[0].name",
                "records[1].name",
                "records[2].name",
            ),
            limitation_paths=("unresolved_claims[0]",),
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_provider_diagnostics() -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KT, 삼성, LG입니다. provider error: upstream timeout",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
        ),
    )

    assert result.code == "raw_contract_exposed"


def test_guard_rejects_unprojected_name_and_korean_address() -> None:
    value = _neutral_records_input(question="Return the first record.").model_copy(
        update={
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=("records[0].name",),
                    identity_paths=("records[0].name",),
                ),
            ),
        }
    )
    name = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha and delta.",
            cited_paths=("records[0].name",),
        ),
    )
    address = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha. Address: unprojected location.",
            cited_paths=("records[0].name",),
        ),
    )

    assert name.code == "ungrounded_text"
    assert address.code == "ungrounded_text"


def test_guard_accepts_grounded_english_multiword_name() -> None:
    value = CompositionInputV1(
        request_id="request-english",
        question="What is the first record?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-records"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        composition_list_root="records",
        public_facts={"records": [{"name": "alpha beta"}]},
        structural_evidence_relations=(
            StructuralEvidenceRelationV1(
                evidence_paths=("records[0].name",),
                identity_paths=("records[0].name",),
            ),
        ),
    )
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha beta.",
            cited_paths=("records[0].name",),
        ),
    )

    assert result.accepted is True


def test_guard_requires_string_identity_for_each_top_n_item() -> None:
    value = _neutral_records_input().model_copy(
        update={
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=(
                        "records[0].value",
                        "records[1].value",
                        "records[2].value",
                    ),
                    identity_paths=(),
                ),
            )
        }
    )
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="59, 58, 57.",
            cited_paths=(
                "records[0].value",
                "records[1].value",
                "records[2].value",
            ),
        ),
    )

    assert result.code == "requested_item_identity_not_cited"


@pytest.mark.parametrize(
    "suffix",
    ["住所東京都新宿區", "секрет Москва", "سر القاهرة"],
)
def test_guard_rejects_unprojected_unicode_text(suffix: str) -> None:
    result = guard_final_response(
        _neutral_records_input(question="Return alpha.").model_copy(
            update={
                "citable_paths": ("records[0].name",),
                "structural_evidence_relations": (),
            }
        ),
        DraftResponseV1(
            content=f"alpha. {suffix}",
            cited_paths=("records[0].name",),
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_unprojected_symbols_and_semantic_exclusion() -> None:
    value = _neutral_records_input(question="Return alpha.").model_copy(
        update={
            "citable_paths": ("records[0].name",),
            "structural_evidence_relations": (),
        }
    )
    symbol = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha. 🔐🏠",
            cited_paths=("records[0].name",),
        ),
    )
    exclusion = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha is outside the requested set.",
            cited_paths=("records[0].name",),
        ),
    )

    assert symbol.code == "ungrounded_symbol"
    assert exclusion.code == "ungrounded_text"


def test_guard_accepts_synthetic_neutral_projected_literals() -> None:
    value = CompositionInputV1(
        request_id="request-synthetic-fields",
        question="Return the projected fields.",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="skill", name="neutral-record"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts={
            "field_alpha": "token_alpha",
            "field_beta": 200,
            "field_gamma": "token_gamma",
        },
        resolved_claims=("aggregate",),
        citable_paths=("field_alpha", "field_beta", "field_gamma"),
    )
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="token_alpha, 200, token_gamma.",
            cited_paths=("field_alpha", "field_beta", "field_gamma"),
        ),
    )

    assert result.accepted is True

    status_value = value.model_copy(
        update={
            "question": "Return the current state.",
            "public_facts_json": '{"state":"ready"}',
            "citable_paths": ("state",),
        }
    )
    status_result = guard_final_response(
        status_value,
        DraftResponseV1(
            content="ready.",
            cited_paths=("state",),
        ),
    )

    assert status_result.accepted is True


@pytest.mark.parametrize(
    ("question", "content", "cited_paths"),
    [
            (
                "현재 주가는 얼마인가요?",
                "현재 USD 200입니다. 주가하락입니다.",
                ("currency", "price"),
        ),
        (
            "현재 상태를 알려줘",
            "현재 상태는 정상입니다. 상태불량입니다.",
            ("status",),
        ),
    ],
)
def test_guard_rejects_question_prefix_fact_expansion(
    question: str,
    content: str,
    cited_paths: tuple[str, ...],
) -> None:
    value = CompositionInputV1(
        request_id="request-prefix-expansion",
        question=question,
        locale="ko-KR",
        selected_route="react",
        asset_ref=AssetRefV1(type="skill", name="generic-status"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts={
            "price": 200,
            "currency": "USD",
            "status": "정상",
        },
        citable_paths=cited_paths,
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=cited_paths),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_exact_question_terms_used_as_uncited_fact() -> None:
    value = _neutral_records_input(
        question="Is alpha outside the requested set?"
    ).model_copy(
        update={
            "citable_paths": ("records[0].name",),
            "structural_evidence_relations": (),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha is outside the requested set.",
            cited_paths=("records[0].name",),
        ),
    )

    assert result.code == "ungrounded_text"


@pytest.mark.parametrize(
    "content",
    ["alpha cannot be verified.", "alpha is unverified."],
)
def test_guard_rejects_limitation_language_without_unresolved_claims(
    content: str,
) -> None:
    result = guard_final_response(
        _neutral_records_input(question="Return alpha.").model_copy(
            update={
                "citable_paths": ("records[0].name",),
                "structural_evidence_relations": (),
            }
        ),
        DraftResponseV1(
            content=content,
            cited_paths=("records[0].name",),
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_enforces_domain_neutral_count_only_scope() -> None:
    value = _input().model_copy(
        update={
            "question": "3팀만 알려줘",
            "public_facts_json": (
                '{"data":{"items":['
                '{"rank":1,"team":"KT"},'
                '{"rank":2,"team":"삼성"},'
                '{"rank":3,"team":"LG"},'
                '{"rank":4,"team":"두산"}'
                "]}}"
            ),
        }
    )
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="KT 1, 삼성 2, LG 3, 두산 4입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[0].rank",
                "data.items[1].team",
                "data.items[1].rank",
                "data.items[2].team",
                "data.items[2].rank",
                "data.items[3].team",
                "data.items[3].rank",
            ),
        ),
    )

    assert result.code == "citation_outside_requested_scope"


@pytest.mark.parametrize(
    "question",
    [
        "Tell me the first 3 teams",
        "Tell me the first3 teams",
        "Tell me the first3teams",
        "Tell me the top3teams",
        "Tell me the top 3teams",
    ],
)
def test_guard_enforces_english_compact_top_n_scope(question: str) -> None:
    value = _neutral_records_input().model_copy(
        update={
            "question": question,
            "public_facts_json": (
                '{"records":['
                '{"name":"alpha","value":59},'
                '{"name":"beta","value":58},'
                '{"name":"gamma","value":57},'
                '{"name":"delta","value":56}'
                "]}"
            ),
        }
    )
    four_paths = tuple(
        f"records[{index}].{field}"
        for index in range(4)
        for field in ("name", "value")
    )
    three_paths = four_paths[:6]

    rejected = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57, delta, 56.",
            cited_paths=four_paths,
        ),
    )
    accepted = guard_final_response(
        value,
        DraftResponseV1(
            content="alpha, 59, beta, 58, gamma, 57.",
            cited_paths=three_paths,
        ),
    )

    assert rejected.code == "citation_outside_requested_scope"
    assert accepted.accepted is True


@pytest.mark.parametrize(
    "content",
    [
        "first 3 wins: KT는 59, 삼성은 58, LG는 57입니다.",
        "top 3 wins: KT는 59, 삼성은 58, LG는 57입니다.",
    ],
)
def test_guard_rejects_english_top_n_classifier_mismatch(content: str) -> None:
    value = _input().model_copy(
        update={
            "question": "Tell me the first 3 teams",
            "public_facts_json": (
                '{"data":{"items":['
                '{"team":"KT","wins":59},'
                '{"team":"삼성","wins":58},'
                '{"team":"LG","wins":57},'
                '{"team":"두산","wins":56}'
                "]}}"
            ),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=tuple(
                f"data.items[{index}].{field}"
                for index in range(3)
                for field in ("team", "wins")
            ),
        ),
    )

    assert result.code == "ungrounded_text"


@pytest.mark.parametrize(
    "content",
    [
        "alpha, 57, beta, 59.",
        "alpha is beta.",
        "alpha equals beta.",
    ],
)
def test_guard_rejects_cross_path_relation_reassembly(content: str) -> None:
    value = _neutral_records_input(
        question="Return the first 2 records with their values."
    ).model_copy(
        update={
            "public_facts_json": (
                '{"records":['
                '{"name":"alpha","value":59},'
                '{"name":"beta","value":57}'
                "]}"
            ),
        }
    )
    cited_paths = (
        "records[0].name",
        "records[1].name",
    )
    if "57" in content:
        cited_paths = (
            "records[0].name",
            "records[0].value",
            "records[1].name",
            "records[1].value",
        )
    value = value.model_copy(
        update={
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=cited_paths,
                    identity_paths=tuple(
                        path for path in cited_paths if path.endswith(".name")
                    ),
                ),
            )
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=cited_paths),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize(
    "content",
    [
        "alpha, beta. alpha is beta.",
        "alpha, 59, beta, 57, alpha, 57, beta, 59.",
    ],
)
def test_guard_rejects_canonical_decoy_prefix_and_duplicate_tail(
    content: str,
) -> None:
    value = _neutral_records_input(
        question="Return the first 2 records with their values."
    ).model_copy(
        update={
            "public_facts_json": (
                '{"records":['
                '{"name":"alpha","value":59},'
                '{"name":"beta","value":57}'
                "]}"
            ),
        }
    )
    cited_paths = (
        "records[0].name",
        "records[1].name",
    )
    if "59" in content:
        cited_paths = (
            "records[0].name",
            "records[0].value",
            "records[1].name",
            "records[1].value",
        )
    value = value.model_copy(
        update={
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=cited_paths,
                    identity_paths=tuple(
                        path for path in cited_paths if path.endswith(".name")
                    ),
                ),
            )
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=cited_paths),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize(
    "rendered_number",
    ["-59", "+59", "59%", "59.0"],
)
def test_guard_rejects_numeric_sign_or_unit_reinterpretation(
    rendered_number: str,
) -> None:
    result = guard_final_response(
        _neutral_records_input(question="What is alpha's value?").model_copy(
            update={
                "citable_paths": (
                    "records[0].name",
                    "records[0].value",
                ),
                "structural_evidence_relations": (),
            }
        ),
        DraftResponseV1(
            content=f"alpha, {rendered_number}.",
            cited_paths=(
                "records[0].name",
                "records[0].value",
            ),
        ),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize(
    ("names", "content"),
    [(("a", "b"), "ab."), (("alpha", "beta"), "alphabeta.")],
)
def test_guard_requires_separator_between_cited_literals(
    names: tuple[str, str],
    content: str,
) -> None:
    value = _neutral_records_input(question="Return the first 2 records.").model_copy(
        update={
            "composition_list_root": "records",
            "public_facts_json": (
                '{"records":['
                f'{{"name":"{names[0]}"}},'
                f'{{"name":"{names[1]}"}}'
                "]}"
            ),
            "structural_evidence_relations": (
                StructuralEvidenceRelationV1(
                    evidence_paths=("records[0].name", "records[1].name"),
                    identity_paths=("records[0].name", "records[1].name"),
                ),
            ),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=("records[0].name", "records[1].name"),
        ),
    )

    assert result.code in {
        "cited_value_not_rendered",
        "cited_value_order_mismatch",
    }
