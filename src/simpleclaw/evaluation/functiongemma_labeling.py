"""production-native planner 결과를 side effect 없이 weak label로 축소한다."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from simpleclaw.agent.planner_catalog import PlannerAsset
from simpleclaw.agent.turn_plan import UnifiedTurnPlan
from simpleclaw.evaluation.functiongemma_contract import (
    MAX_CANDIDATES,
    NO_ASSET,
    CandidateAsset,
    CompactIntentCall,
    FunctionCallContractError,
    candidate_id,
    parse_function_call,
)
from simpleclaw.evaluation.functiongemma_dataset import SanitizedCase

MAX_PROVIDER_CALLS = 300
MAX_PROVIDER_SECONDS = 60 * 60
PlannerCallable = Callable[
    [SanitizedCase, tuple[CandidateAsset, ...]], Awaitable[UnifiedTurnPlan]
]


@dataclass(frozen=True)
class LabelingBudget:
    max_calls: int = MAX_PROVIDER_CALLS
    max_seconds: float = MAX_PROVIDER_SECONDS

    def __post_init__(self) -> None:
        if not 1 <= self.max_calls <= MAX_PROVIDER_CALLS:
            raise ValueError("max_calls exceeds provider hard cap")
        if not 0 < self.max_seconds <= MAX_PROVIDER_SECONDS:
            raise ValueError("max_seconds exceeds provider hard cap")


@dataclass(frozen=True)
class LabeledCase:
    case: SanitizedCase
    candidates: tuple[CandidateAsset, ...]
    label: CompactIntentCall
    candidate_fingerprint: str
    confidence: float


@dataclass(frozen=True)
class AdjudicationItem:
    case_id: str
    source_group_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class LabelingResult:
    labeled: tuple[LabeledCase, ...]
    adjudication_queue: tuple[AdjudicationItem, ...]
    provider_calls: int
    elapsed_seconds: float


def _overlap_score(case: SanitizedCase, asset: PlannerAsset) -> tuple[int, str]:
    haystack = case.current.lower()
    terms = (*asset.domains, *asset.intents, asset.name)
    score = sum(1 for term in terms if term and term.lower() in haystack)
    return score, candidate_id(asset.asset_type, asset.name)


def select_candidate_assets(
    case: SanitizedCase,
    catalog_assets: Iterable[PlannerAsset],
) -> tuple[CandidateAsset, ...]:
    """metadata 유사 hard negative를 호출 전에 deterministic하게 고정한다."""
    ranked = sorted(
        catalog_assets,
        key=lambda asset: (
            -_overlap_score(case, asset)[0],
            _overlap_score(case, asset)[1],
        ),
    )
    selected = ranked[: MAX_CANDIDATES - 1]
    candidates = [
        CandidateAsset(
            asset_id=candidate_id(asset.asset_type, asset.name),
            asset_type=asset.asset_type,
            name=asset.name,
            description=asset.description,
            domains=asset.domains,
            intents=asset.intents,
        )
        for asset in selected
    ]
    candidates.append(CandidateAsset(
        asset_id=NO_ASSET,
        asset_type="none",
        name=NO_ASSET,
        description="No suitable candidate; request runtime fallback.",
    ))
    return tuple(candidates)


def candidate_fingerprint(candidates: Sequence[CandidateAsset]) -> str:
    canonical = "\n".join(candidate.asset_id for candidate in candidates)
    return hashlib.sha256(canonical.encode()).hexdigest()


def compact_from_unified_plan(
    plan: UnifiedTurnPlan,
    *,
    candidates: Sequence[CandidateAsset],
) -> CompactIntentCall:
    primary = plan.execution.primary_asset
    primary_id = (
        NO_ASSET
        if primary is None
        else candidate_id(primary.asset_type, primary.name)
    )
    fallback = (
        primary_id == NO_ASSET
        and plan.execution.mode.value in {"execute_asset", "recipe", "tool_loop"}
    )
    payload = {
        "context_relation": plan.context.relation.value,
        "execution_mode": plan.execution.mode.value,
        "domains": list(plan.domains),
        "intents": list(plan.intents),
        "primary_asset": primary_id,
        "fallback_required": fallback,
    }
    return parse_function_call(
        payload,
        candidate_ids=[
            candidate.asset_id
            for candidate in candidates
            if candidate.asset_id != NO_ASSET
        ],
    )


async def label_cases(
    cases: Sequence[SanitizedCase],
    *,
    catalog_assets: Sequence[PlannerAsset],
    planner: PlannerCallable,
    allow_provider_calls: bool,
    budget: LabelingBudget | None = None,
    minimum_confidence: float = 0.55,
) -> LabelingResult:
    """sanitized case만 planner에 보내고 오류/저신뢰 결과를 queue로 분리한다."""
    if not allow_provider_calls:
        raise PermissionError("provider calls require explicit opt-in")
    effective_budget = budget or LabelingBudget()
    started = time.monotonic()
    calls = 0
    labeled: list[LabeledCase] = []
    queue: list[AdjudicationItem] = []
    for case in cases:
        if (
            calls >= effective_budget.max_calls
            or time.monotonic() - started >= effective_budget.max_seconds
        ):
            queue.append(AdjudicationItem(
                case_id=case.case_id,
                source_group_id=case.source_group_id,
                reason_codes=("budget.exhausted",),
            ))
            continue
        candidates = select_candidate_assets(case, catalog_assets)
        fingerprint = candidate_fingerprint(candidates)
        calls += 1
        reasons: list[str] = []
        try:
            plan = await planner(case, candidates)
            compact = compact_from_unified_plan(plan, candidates=candidates)
            if plan.confidence < minimum_confidence:
                reasons.append("confidence.low")
            if plan.execution.mode.value == "recipe" and "create" in plan.intents:
                reasons.append("boundary.creation_vs_execution")
        except FunctionCallContractError as exc:
            queue.append(AdjudicationItem(
                case_id=case.case_id,
                source_group_id=case.source_group_id,
                reason_codes=(exc.code,),
            ))
            continue
        except Exception as exc:  # noqa: BLE001 - private 원문 없이 종류만 queue에 남김.
            queue.append(AdjudicationItem(
                case_id=case.case_id,
                source_group_id=case.source_group_id,
                reason_codes=(f"planner.{type(exc).__name__}",),
            ))
            continue
        if reasons:
            queue.append(AdjudicationItem(
                case_id=case.case_id,
                source_group_id=case.source_group_id,
                reason_codes=tuple(reasons),
            ))
            continue
        labeled.append(LabeledCase(
            case=case,
            candidates=candidates,
            label=compact,
            candidate_fingerprint=fingerprint,
            confidence=plan.confidence,
        ))
    return LabelingResult(
        labeled=tuple(labeled),
        adjudication_queue=tuple(queue),
        provider_calls=calls,
        elapsed_seconds=time.monotonic() - started,
    )


def labeling_public_summary(result: LabelingResult) -> dict[str, Any]:
    """원문·label payload 없이 공개 가능한 aggregate만 반환한다."""
    reasons: dict[str, int] = {}
    for item in result.adjudication_queue:
        for reason in item.reason_codes:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "provider_calls": result.provider_calls,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "labeled_count": len(result.labeled),
        "adjudication_count": len(result.adjudication_queue),
        "adjudication_reasons": dict(sorted(reasons.items())),
    }
