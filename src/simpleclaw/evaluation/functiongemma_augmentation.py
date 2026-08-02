"""FunctionGemma train-only bounded augmentation."""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import replace

from simpleclaw.evaluation.functiongemma_contract import NO_ASSET
from simpleclaw.evaluation.functiongemma_labeling import (
    LabeledCase,
    candidate_fingerprint,
)

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


_PLACEHOLDERS = (
    "<email>", "<phone>", "<url>", "<private_path>", "<identifier>", "<credential>",
)
_STRATA = (
    "entity_placeholder",
    "recipe_creation",
    "recipe_execution",
    "no_asset_ood",
    "spoken",
    "typo",
    "elliptical_followup",
    "topic_shift",
)


def _entity_placeholder(text: str) -> str:
    for index, placeholder in enumerate(_PLACEHOLDERS):
        if placeholder in text:
            return text.replace(
                placeholder,
                _PLACEHOLDERS[(index + 1) % len(_PLACEHOLDERS)],
                1,
            )
    return f"{text} 대상은 <identifier>로 익명화해줘"


def _variant(item: LabeledCase, stratum: str) -> LabeledCase:
    text = item.case.current
    label = item.label
    candidates = item.candidates
    if stratum == "entity_placeholder":
        text = _entity_placeholder(text)
    elif stratum == "recipe_creation":
        text = f"이 요청을 새 레시피로 만들어줘: {text}"
        label = replace(
            label,
            execution_mode="direct_answer",
            intents=("create_recipe",),
            primary_asset=NO_ASSET,
            fallback_required=False,
        )
    elif stratum == "recipe_execution":
        recipes = tuple(
            candidate for candidate in candidates
            if candidate.asset_type == "recipe"
        )
        if recipes:
            text = f"{recipes[0].name} 레시피를 실행해줘"
            label = replace(
                label,
                execution_mode="direct_answer",
                intents=("execute_recipe",),
                primary_asset=recipes[0].asset_id,
                fallback_required=False,
            )
        else:
            text = f"등록되지 않은 레시피를 실행해줘: {text}"
            label = replace(
                label,
                execution_mode="direct_answer",
                intents=("execute_recipe",),
                primary_asset=NO_ASSET,
                fallback_required=True,
            )
    elif stratum == "no_asset_ood":
        text = f"후보에 없는 작업을 처리해줘: {text}"
        candidates = tuple(
            candidate for candidate in candidates
            if candidate.asset_id != label.primary_asset
        )
        label = replace(
            label,
            primary_asset=NO_ASSET,
            fallback_required=True,
        )
    elif stratum == "spoken":
        text = _polite_to_spoken(text)
    elif stratum == "typo":
        text = _typo(text)
    elif stratum == "elliptical_followup":
        text = _elliptical(text)
    elif stratum == "topic_shift":
        text = _topic_shift(text)
        label = replace(label, context_relation="topic_shift")
    if text == item.case.current:
        text += " "
    return replace(
        item,
        case=replace(item.case, current=text),
        candidates=candidates,
        label=label,
        candidate_fingerprint=candidate_fingerprint(candidates),
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
    augmented: list[LabeledCase] = []
    ordered = sorted(labeled, key=lambda item: item.case.case_id)
    train_items: list[LabeledCase] = []
    for item in ordered:
        if item.case.split != "train":
            continue
        if item.case.source_fingerprint in forbidden_source_fingerprints:
            raise ValueError("sealed/dev/test source cannot seed augmentation")
        train_items.append(item)
    rng = random.Random(seed)
    strata = list(_STRATA)
    rng.shuffle(strata)
    for source_index, item in enumerate(train_items):
        start = (source_index * max_per_source) % len(strata)
        selected = [
            strata[(start + offset) % len(strata)]
            for offset in range(max_per_source)
        ]
        for index, stratum in enumerate(selected):
            if len(augmented) >= max_total:
                return tuple(augmented)
            variant = _variant(item, stratum)
            case = replace(
                variant.case,
                case_id=f"{item.case.case_id}:aug:{index}:{stratum}",
            )
            augmented.append(replace(variant, case=case))
    return tuple(augmented)
