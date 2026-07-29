"""BIZ-512 train-only augmentation 경계."""

from __future__ import annotations

from simpleclaw.evaluation.functiongemma_augmentation import augment_train_cases
from simpleclaw.evaluation.functiongemma_contract import (
    NO_ASSET,
    CompactIntentCall,
)
from simpleclaw.evaluation.functiongemma_dataset import SanitizedCase
from simpleclaw.evaluation.functiongemma_labeling import LabeledCase


def _labeled(split: str, number: int) -> LabeledCase:
    case = SanitizedCase(
        f"case:{number}", f"group:{number}", (), "알려주세요",
        "telegram", f"fp:{number}", split,
    )
    return LabeledCase(
        case=case,
        candidates=(),
        label=CompactIntentCall(
            "standalone", "direct_answer", (), ("explain",), NO_ASSET, False
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
