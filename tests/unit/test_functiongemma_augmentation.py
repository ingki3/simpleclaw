"""BIZ-512 train-only augmentation 경계."""

from __future__ import annotations

from simpleclaw.evaluation.functiongemma_augmentation import augment_train_cases
from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CandidateAsset,
    CompactIntentCall,
    parse_function_call,
)
from simpleclaw.evaluation.functiongemma_dataset import SanitizedCase
from simpleclaw.evaluation.functiongemma_labeling import LabeledCase


def _labeled(split: str, number: int, *, with_recipe: bool = False) -> LabeledCase:
    case = SanitizedCase(
        f"case:{number}", f"group:{number}", (), "알려주세요",
        "telegram", f"fp:{number}", split,
    )
    return LabeledCase(
        case=case,
        candidates=(
            CandidateAsset(
                "recipe:daily", "recipe", "daily", "daily recipe"
            ),
        ) if with_recipe else (),
        label=CompactIntentCall(
            "standalone",
            "direct_answer",
            (),
            ("execute_recipe",) if with_recipe else ("explain",),
            "recipe:daily" if with_recipe else NO_ASSET,
            False,
        ),
        candidate_fingerprint="catalog",
        confidence=0.9,
    )


def test_augmentation_is_train_only_deterministic_and_bounded() -> None:
    items = [_labeled("train", 1), _labeled("dev", 2), _labeled("test", 3)]
    first = augment_train_cases(items)
    second = augment_train_cases(items)
    assert first == second
    assert len(first) == 4
    assert {item.case.source_group_id for item in first} == {"group:1"}
    assert all(item.case.split == "train" for item in first)
    assert any(item.label.context_relation == "topic_shift" for item in first)


def test_global_cap_and_forbidden_seed() -> None:
    items = [_labeled("train", index) for index in range(5)]
    assert len(augment_train_cases(items, max_total=3)) == 3
    try:
        augment_train_cases(
            items,
            forbidden_source_fingerprints=frozenset({"fp:0"}),
        )
    except ValueError as exc:
        assert "sealed" in str(exc)
    else:
        raise AssertionError("forbidden seed must fail")


def test_required_strata_update_text_label_candidates_and_fallback() -> None:
    items = [
        _labeled("train", index, with_recipe=True)
        for index in range(1, 4)
    ]
    augmented = augment_train_cases(items)
    by_stratum = {
        item.case.case_id.rsplit(":", 1)[-1]: item
        for item in augmented
    }
    assert {
        "entity_placeholder",
        "recipe_creation",
        "recipe_execution",
        "no_asset_ood",
    } <= set(by_stratum)

    entity = by_stratum["entity_placeholder"]
    assert "<identifier>" in entity.case.current
    assert "private-" not in entity.case.current

    creation = by_stratum["recipe_creation"]
    assert creation.label.execution_mode == "direct_answer"
    assert creation.label.primary_asset == NO_ASSET
    assert not creation.label.fallback_required

    execution = by_stratum["recipe_execution"]
    assert execution.label.execution_mode == "direct_answer"
    assert execution.label.primary_asset == "recipe:daily"
    assert not execution.label.fallback_required

    ood = by_stratum["no_asset_ood"]
    assert ood.label.primary_asset == NO_ASSET
    assert ood.label.fallback_required
    assert all(
        candidate.asset_id != "recipe:daily"
        for candidate in ood.candidates
    )
    for item in (creation, execution, ood):
        parsed = parse_function_call(
            item.label.to_arguments(),
            candidate_ids=[candidate.asset_id for candidate in item.candidates],
        )
        assert parsed == item.label


def test_required_strata_coverage_is_deterministic_within_caps() -> None:
    items = [_labeled("train", index) for index in range(10)]
    first = augment_train_cases(items, max_total=17)
    second = augment_train_cases(items, max_total=17)
    assert first == second
    assert len(first) == 17
    assert all(
        sum(item.case.source_group_id == source for item in first) <= 4
        for source in {item.case.source_group_id for item in first}
    )
    assert {
        item.case.case_id.rsplit(":", 1)[-1] for item in first
    } == {
        "entity_placeholder",
        "recipe_creation",
        "recipe_execution",
        "no_asset_ood",
        "spoken",
        "typo",
        "elliptical_followup",
        "topic_shift",
    }
