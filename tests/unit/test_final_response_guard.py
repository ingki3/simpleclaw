"""BIZ-628 — 중앙 final response grounding guard."""

from __future__ import annotations

import pytest

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
            content=(
                "현재 KBO 상위 3팀은 KT, 삼성, LG입니다. "
                "각각 59승, 58승, 57승입니다."
            ),
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
            content="KT는 99승입니다.",
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
            content="현재 상위 3팀은 KT, 삼성, LG이며 삼성은 58승입니다.",
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
            content="현재 상위 3팀은 KT, 삼성, LG로 확정입니다.",
            cited_paths=(
                "data.items[0].team",
                "data.items[1].team",
                "data.items[2].team",
            ),
            limitation_paths=("unresolved_claims[0]",),
        ),
    )

    assert certain.code == "limitation_not_rendered"


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
            content="The current top team is New York Yankees.",
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


def test_guard_accepts_domain_neutral_question_vocabulary() -> None:
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
            content="현재 Apple 주가는 200 USD입니다.",
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
            content="현재 상태는 정상입니다.",
            cited_paths=("status",),
        ),
    )

    assert status_result.accepted is True
