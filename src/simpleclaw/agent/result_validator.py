"""Exact/4-mode 모든 경로가 공유하는 Evidence/Action validator."""

from __future__ import annotations

from dataclasses import dataclass

from simpleclaw.agent.resolution_ledger import ResolutionLedger
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    GoalResolutionState,
    GoalStatus,
)


@dataclass(frozen=True)
class ValidationDecision:
    """Final composer가 사용할 수 있는 claim과 차단 사유."""

    allow_final: bool
    supported_claims: tuple[str, ...]
    blocked_claims: tuple[str, ...]
    limitations: tuple[str, ...]
    action_state: str


class CommonResultValidator:
    """Collector 역할 없이 누적 ledger의 충분성/안전성만 검증한다."""

    def validate(
        self,
        *,
        goal: GoalResolutionState,
        ledger: ResolutionLedger,
        required_claims: tuple[str, ...] = (),
    ) -> ValidationDecision:
        limitations: list[str] = []
        supported = set(goal.resolved_claims) if not required_claims else set()
        values_by_claim: dict[str, set[str]] = {}
        for evidence in ledger.evidence:
            if not evidence.usable:
                limitations.append(
                    evidence.limitation or f"unusable_evidence:{evidence.claim_id}"
                )
                continue
            if not evidence.source_url and not evidence.provenance:
                limitations.append(f"missing_provenance:{evidence.claim_id}")
                continue
            if evidence.fresh is False:
                limitations.append(f"stale_evidence:{evidence.claim_id}")
                continue
            if evidence.claim_id:
                supported.add(evidence.claim_id)
                values_by_claim.setdefault(evidence.claim_id, set()).add(
                    repr(evidence.value)
                )
        for claim_id, values in values_by_claim.items():
            if len(values) > 1:
                supported.discard(claim_id)
                limitations.append(f"evidence_conflict:{claim_id}")

        action_state = "none"
        for result in ledger.asset_results:
            if not result.side_effect:
                continue
            if result.status is AssetExecutionStatus.UNKNOWN_EFFECT:
                action_state = "unknown_effect"
                limitations.append("action_effect_unknown_no_retry")
            elif result.status is AssetExecutionStatus.PARTIAL_SUCCESS:
                action_state = "partial_success"
                limitations.append("action_partial_success_no_retry")
            elif result.status is AssetExecutionStatus.COMPLETED and result.effect_id:
                if action_state == "none":
                    action_state = "completed"
            else:
                action_state = "unverified"
                limitations.append("action_effect_unverified")

        required = tuple(dict.fromkeys(required_claims))
        blocked = tuple(claim for claim in required if claim not in supported)
        if blocked:
            limitations.extend(f"unsupported_claim:{claim}" for claim in blocked)
        allow_final = (
            goal.status is GoalStatus.RESOLVED
            and not blocked
            and action_state not in {"unknown_effect", "partial_success", "unverified"}
        )
        return ValidationDecision(
            allow_final=allow_final,
            supported_claims=tuple(sorted(supported)),
            blocked_claims=blocked,
            limitations=tuple(dict.fromkeys(limitations)),
            action_state=action_state,
        )


# 계획/테스트에서 사용한 명칭을 public alias로 유지한다.
ResultValidator = CommonResultValidator
