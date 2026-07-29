"""FunctionGemma train-only bounded augmentation."""

from __future__ import annotations

import random
import re
from dataclasses import replace
from typing import Callable, Sequence

from simpleclaw.evaluation.functiongemma_labeling import LabeledCase

MAX_AUGMENTATIONS_PER_SOURCE = 4
MAX_AUGMENTED_TRAIN = 1000


def _polite_to_spoken(text: str) -> str:
    return re.sub(r"(해주세요|해 주세요|알려주세요)[.!]?$", "해줘", text)


def _typo(text: str) -> str:
    replacements = (("해주세요", "해주세여"), ("알려줘", "알려죠"), ("실행", "실헹"))
    for before, after in replacements:
        if before in text:
            return text.replace(before, after, 1)
    return text + "요"


def _elliptical(text: str) -> str:
    return "그거 " + text if not text.startswith(("그", "이")) else text


def _topic_shift(text: str) -> str:
    return "아, 다른 얘긴데 " + text


_TRANSFORMS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("spoken", _polite_to_spoken),
    ("typo", _typo),
    ("elliptical_followup", _elliptical),
    ("topic_shift", _topic_shift),
)


def augment_train_cases(
    labeled: Sequence[LabeledCase],
    *,
    seed: int = 42,
    max_per_source: int = MAX_AUGMENTATIONS_PER_SOURCE,
    max_total: int = MAX_AUGMENTED_TRAIN,
    forbidden_source_fingerprints: frozenset[str] = frozenset(),
) -> tuple[LabeledCase, ...]:
    """train source만 변형하고 group ID를 보존하며 dev/test/sealed seed를 거부한다."""
    if not 0 <= max_per_source <= MAX_AUGMENTATIONS_PER_SOURCE:
        raise ValueError("max_per_source exceeds hard cap")
    if not 0 <= max_total <= MAX_AUGMENTED_TRAIN:
        raise ValueError("max_total exceeds hard cap")
    rng = random.Random(seed)
    augmented: list[LabeledCase] = []
    ordered = sorted(labeled, key=lambda item: item.case.case_id)
    for item in ordered:
        if item.case.split != "train":
            continue
        if item.case.source_fingerprint in forbidden_source_fingerprints:
            raise ValueError("sealed/dev/test source cannot seed augmentation")
        transforms = list(_TRANSFORMS)
        rng.shuffle(transforms)
        for index, (stratum, transform) in enumerate(transforms[:max_per_source]):
            if len(augmented) >= max_total:
                return tuple(augmented)
            current = transform(item.case.current)
            if current == item.case.current:
                current += " "
            case = replace(
                item.case,
                case_id=f"{item.case.case_id}:aug:{index}:{stratum}",
                current=current,
            )
            label = item.label
            if stratum == "topic_shift":
                label = replace(label, context_relation="topic_shift")
            augmented.append(replace(item, case=case, label=label))
    return tuple(augmented)
