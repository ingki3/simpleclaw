"""BIZ-628 — 중앙 final response grounding guard."""

from __future__ import annotations

import pytest

from simpleclaw.agent.composition_citations import canonicalize_draft_citations
from simpleclaw.agent.composition_contracts import (
    CompositionInputV1,
    DraftResponseV1,
)
from simpleclaw.agent.final_response_guard import guard_final_response
from simpleclaw.graph_runtime.contracts import AssetRefV1
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus


def _input() -> CompositionInputV1:
    return CompositionInputV1(
        request_id="request-1",
        question="현재 KBO 상위 3팀과 승수만 알려줘",
        locale="ko-KR",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
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


def test_guard_accepts_grounded_natural_response() -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content="KBO: KT 59, 삼성 58, LG 57입니다.",
            cited_paths=(
                "data.category",
                "data.items[0].team",
                "data.items[0].wins",
                "data.items[1].team",
                "data.items[1].wins",
                "data.items[2].team",
                "data.items[2].wins",
            ),
        ),
    )

    assert result.accepted is True


def test_guard_defensively_rejects_empty_citations() -> None:
    draft = DraftResponseV1.model_construct(
        content="KT입니다.",
        cited_paths=(),
        limitation_paths=(),
    )

    result = guard_final_response(_input(), draft)

    assert result.code == "citations_required"


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
    number = guard_final_response(
        _input().model_copy(update={"question": "KT 승수를 알려줘"}),
        DraftResponseV1(
            content="KT는 99입니다.",
            cited_paths=("data.items[0].team",),
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

    assert partial.code == "requested_scope_not_fully_cited"
    assert uncited.code == "ungrounded_number"
    assert private.code == "raw_contract_exposed"


def test_guard_requires_visible_limitation_for_every_unresolved_claim() -> None:
    value = _input().model_copy(update={"unresolved_claims": ("동률 여부",)})
    certain = guard_final_response(
        value,
        DraftResponseV1(
            content="KT, 삼성, LG입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
            limitation_paths=("unresolved_claims[0]",),
        ),
    )

    assert certain.code == "limitation_not_rendered"


def test_guard_accepts_limitation_language_for_declared_unresolved_claim() -> None:
    value = _input().model_copy(update={"unresolved_claims": ("동률 여부",)})

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="KT, 삼성, LG입니다. 확인할 수 없습니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
            limitation_paths=("unresolved_claims[0]",),
        ),
    )

    assert result.accepted is True


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
    name = guard_final_response(
        _input().model_copy(update={"question": "현재 상위 1팀"}),
        DraftResponseV1(
            content="현재 상위 팀은 KT이며 두산도 포함됩니다.",
            cited_paths=("data.items[0].team",),
        ),
    )
    address = guard_final_response(
        _input().model_copy(update={"question": "현재 상위 1팀"}),
        DraftResponseV1(
            content="현재 상위 팀은 KT입니다. 주소는 서울시 강남구 역삼동입니다.",
            cited_paths=("data.items[0].team",),
        ),
    )

    assert name.code == "ungrounded_text"
    assert address.code == "ungrounded_text"


def test_guard_accepts_grounded_english_multiword_name() -> None:
    value = CompositionInputV1(
        request_id="request-english",
        question="Who is the top 1 team?",
        locale="en-US",
        selected_route="recipe",
        asset_ref=AssetRefV1(type="recipe", name="sports-live"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts={"items": [{"team": "New York Yankees"}]},
    )
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="New York Yankees.",
            cited_paths=("items[0].team",),
        ),
    )

    assert result.accepted is True


def test_guard_requires_string_identity_for_each_top_n_item() -> None:
    result = guard_final_response(
        _input(),
        DraftResponseV1(
            content="상위 3팀을 확인했습니다.",
            cited_paths=(
                "data.items[0].rank",
                "data.items[1].rank",
                "data.items[2].rank",
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
        _input().model_copy(update={"question": "현재 상위 1팀"}),
        DraftResponseV1(
            content=f"KT입니다. {suffix}",
            cited_paths=("data.items[0].team",),
        ),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_unprojected_symbols_and_semantic_exclusion() -> None:
    value = _input().model_copy(update={"question": "현재 상위 1팀"})
    symbol = guard_final_response(
        value,
        DraftResponseV1(
            content="KT입니다. 🔐🏠",
            cited_paths=("data.items[0].team",),
        ),
    )
    exclusion = guard_final_response(
        value,
        DraftResponseV1(
            content="KT는 현재 순위 외 팀입니다.",
            cited_paths=("data.items[0].team",),
        ),
    )

    assert symbol.code == "ungrounded_symbol"
    assert exclusion.code == "ungrounded_text"


def test_guard_accepts_domain_neutral_projected_literals() -> None:
    value = CompositionInputV1(
        request_id="request-stock",
        question="현재 Apple 주가는 얼마인가요?",
        locale="ko-KR",
        selected_route="react",
        asset_ref=AssetRefV1(type="skill", name="stock-snapshot"),
        result_status=AssetResultStatus.RESOLVED,
        effect_status=EffectStatus.NONE,
        normalized_payload_hash="payload-hash",
        public_facts={"symbol": "Apple", "price": 200, "currency": "USD"},
    )
    result = guard_final_response(
        value,
        DraftResponseV1(
            content="현재 USD 200 Apple입니다.",
            cited_paths=("symbol", "price", "currency"),
        ),
    )

    assert result.accepted is True

    status_value = value.model_copy(
        update={
            "question": "현재 상태를 알려줘",
            "public_facts_json": '{"status":"정상"}',
        }
    )
    status_result = guard_final_response(
        status_value,
        DraftResponseV1(
            content="현재 정상입니다.",
            cited_paths=("status",),
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
    )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=cited_paths),
    )

    assert result.code == "ungrounded_text"


def test_guard_rejects_exact_question_terms_used_as_uncited_fact() -> None:
    value = _input().model_copy(
        update={"question": "KT가 순위 밖의 팀 맞나요?"}
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content="KT는 순위 밖의 팀입니다.",
            cited_paths=("data.items[0].team",),
        ),
    )

    assert result.code == "ungrounded_text"


@pytest.mark.parametrize(
    "content",
    ["KT는 확인할 수 없습니다.", "KT is unverified."],
)
def test_guard_rejects_limitation_language_without_unresolved_claims(
    content: str,
) -> None:
    result = guard_final_response(
        _input().model_copy(update={"question": "KT를 알려줘"}),
        DraftResponseV1(
            content=content,
            cited_paths=("data.items[0].team",),
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
    "content",
    [
        "KT 57, LG 59입니다.",
        "KT는 LG입니다.",
        "KT is LG.",
    ],
)
def test_guard_rejects_cross_path_relation_reassembly(content: str) -> None:
    value = _input().model_copy(
        update={
            "question": "현재 상위 2팀과 승수",
            "public_facts_json": (
                '{"data":{"items":['
                '{"team":"KT","wins":59},'
                '{"team":"LG","wins":57}'
                "]}}"
            ),
        }
    )
    cited_paths = (
        "data.items[0].team",
        "data.items[1].team",
    )
    if "57" in content:
        cited_paths = (
            "data.items[0].team",
            "data.items[0].wins",
            "data.items[1].team",
            "data.items[1].wins",
        )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=cited_paths),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize(
    "content",
    [
        "KT, LG. KT는 LG입니다.",
        "KT 59, LG 57, KT 57, LG 59입니다.",
    ],
)
def test_guard_rejects_canonical_decoy_prefix_and_duplicate_tail(
    content: str,
) -> None:
    value = _input().model_copy(
        update={
            "question": "현재 상위 2팀과 승수",
            "public_facts_json": (
                '{"data":{"items":['
                '{"team":"KT","wins":59},'
                '{"team":"LG","wins":57}'
                "]}}"
            ),
        }
    )
    cited_paths = (
        "data.items[0].team",
        "data.items[1].team",
    )
    if "59" in content:
        cited_paths = (
            "data.items[0].team",
            "data.items[0].wins",
            "data.items[1].team",
            "data.items[1].wins",
        )

    result = guard_final_response(
        value,
        DraftResponseV1(content=content, cited_paths=cited_paths),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize("rendered_number", ["-59", "+59", "59%"])
def test_guard_rejects_numeric_sign_or_unit_reinterpretation(
    rendered_number: str,
) -> None:
    result = guard_final_response(
        _input().model_copy(update={"question": "KT 승수를 알려줘"}),
        DraftResponseV1(
            content=f"KT {rendered_number}입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[0].wins",
            ),
        ),
    )

    assert result.code == "cited_value_order_mismatch"


@pytest.mark.parametrize(
    ("teams", "content"),
    [(("K", "T"), "KT입니다."), (("KT", "LG"), "KTLG입니다.")],
)
def test_guard_requires_separator_between_cited_literals(
    teams: tuple[str, str],
    content: str,
) -> None:
    value = _input().model_copy(
        update={
            "question": "현재 상위 2팀",
            "public_facts_json": (
                '{"items":['
                f'{{"team":"{teams[0]}"}},'
                f'{{"team":"{teams[1]}"}}'
                "]}"
            ),
        }
    )

    result = guard_final_response(
        value,
        DraftResponseV1(
            content=content,
            cited_paths=("items[0].team", "items[1].team"),
        ),
    )

    assert result.code == "cited_value_order_mismatch"
