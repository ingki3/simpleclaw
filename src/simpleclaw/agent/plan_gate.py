"""UnifiedTurnPlan을 실행 전에 검증하는 순수 로컬 gate.

PlanGate는 사용자 의도나 route를 다시 추론하지 않는다. planner가 반환한 구조를
현재 context 후보, immutable capability catalog, 고정 실행 정책과 대조하고
downstream이 repair/clarify/confirmation/reject 중 하나를 결정할 수 있는 안정적인
결과만 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.turn_plan import (
    ContextRelation,
    EvidenceOwner,
    ExecutionMode,
    UnifiedTurnPlan,
)


class GateStatus(str, Enum):
    """PlanGate가 downstream controller에 요구하는 다음 동작."""

    PASS = "pass"
    CLARIFY = "clarify"
    CONFIRMATION_REQUIRED = "confirmation_required"
    REPAIR = "repair"
    REJECT = "reject"


@dataclass(frozen=True)
class PlanViolation:
    """사용자 원문을 포함하지 않는 안정적인 계획 위반."""

    code: str
    field: str
    message: str


@dataclass(frozen=True)
class PlanGateResult:
    """검증 결과와 실행 가능한 경우의 원래 immutable plan."""

    status: GateStatus
    effective_plan: UnifiedTurnPlan | None
    violations: tuple[PlanViolation, ...] = ()


def _violation(code: str, field: str, message: str) -> PlanViolation:
    return PlanViolation(code=code, field=field, message=message)


def _asset_identity(asset: PlannerAsset) -> tuple[str, str]:
    return asset.asset_type, asset.name


class PlanGate:
    """현재 runtime snapshot만 사용해 UnifiedTurnPlan을 fail-closed 검증한다."""

    def __init__(
        self,
        *,
        selected_context_max_turns: int = 8,
        selected_context_max_chars: int = 6000,
    ) -> None:
        if selected_context_max_turns <= 0:
            raise ValueError("selected_context_max_turns must be greater than zero")
        if selected_context_max_chars <= 0:
            raise ValueError("selected_context_max_chars must be greater than zero")
        self._selected_context_max_turns = selected_context_max_turns
        self._selected_context_max_chars = selected_context_max_chars

    def evaluate(
        self,
        plan: UnifiedTurnPlan,
        *,
        candidates: ContextCandidateSet,
        catalog: PlannerCatalog,
    ) -> PlanGateResult:
        """plan을 검증하고 repair 가능한 오류와 실행 거부를 구분한다."""
        violations: list[PlanViolation] = []
        rejected: list[PlanViolation] = []
        confirmation: list[PlanViolation] = []

        if plan.catalog_fingerprint != catalog.fingerprint:
            violations.append(
                _violation(
                    "catalog.fingerprint_mismatch",
                    "catalog_fingerprint",
                    "Plan catalog snapshot does not match the runtime catalog.",
                )
            )

        self._validate_context(plan, candidates, violations)
        self._validate_execution_and_facts(plan, violations)
        self._validate_catalog_scope(
            plan,
            catalog,
            violations=violations,
            rejected=rejected,
            confirmation=confirmation,
        )

        if rejected:
            return PlanGateResult(
                status=GateStatus.REJECT,
                effective_plan=None,
                violations=tuple((*violations, *rejected)),
            )
        if violations:
            return PlanGateResult(
                status=GateStatus.REPAIR,
                effective_plan=None,
                violations=tuple(violations),
            )
        if confirmation or plan.execution.requires_confirmation:
            if not confirmation:
                confirmation.append(
                    _violation(
                        "execution.confirmation_required",
                        "execution.requires_confirmation",
                        "The plan explicitly requires user confirmation.",
                    )
                )
            return PlanGateResult(
                status=GateStatus.CONFIRMATION_REQUIRED,
                effective_plan=plan,
                violations=tuple(confirmation),
            )
        if (
            plan.context.relation is ContextRelation.UNCLEAR
            or plan.context.unresolved_references
            or plan.clarification.required
        ):
            return PlanGateResult(
                status=GateStatus.CLARIFY,
                effective_plan=plan,
            )
        return PlanGateResult(status=GateStatus.PASS, effective_plan=plan)

    def _validate_context(
        self,
        plan: UnifiedTurnPlan,
        candidates: ContextCandidateSet,
        violations: list[PlanViolation],
    ) -> None:
        context = plan.context
        standalone_relations = {
            ContextRelation.STANDALONE,
            ContextRelation.TOPIC_SHIFT,
        }
        if context.relation in standalone_relations and (
            context.use_prior_context or context.selected_turn_ids
        ):
            violations.append(
                _violation(
                    "context.standalone_has_prior_context",
                    "context",
                    "Standalone and topic-shift plans cannot select prior context.",
                )
            )
        if context.use_prior_context and not context.selected_turn_ids:
            violations.append(
                _violation(
                    "context.prior_context_without_selection",
                    "context.selected_turn_ids",
                    "Prior context use requires at least one selected turn.",
                )
            )
        if context.selected_turn_ids and not context.use_prior_context:
            violations.append(
                _violation(
                    "context.selection_without_prior_context",
                    "context.use_prior_context",
                    "Selected turns require prior-context use to be enabled.",
                )
            )

        by_id = {
            candidate.turn_id: candidate for candidate in candidates.candidates
        }
        unknown = [
            turn_id
            for turn_id in context.selected_turn_ids
            if turn_id not in by_id
        ]
        if unknown:
            violations.append(
                _violation(
                    "context.unknown_selected_turn",
                    "context.selected_turn_ids",
                    "Selected turns must come from the current candidate set.",
                )
            )
        if len(context.selected_turn_ids) > self._selected_context_max_turns:
            violations.append(
                _violation(
                    "context.selected_turn_limit_exceeded",
                    "context.selected_turn_ids",
                    "Selected turn count exceeds the local context budget.",
                )
            )
        selected_chars = sum(
            len(by_id[turn_id].content)
            for turn_id in context.selected_turn_ids
            if turn_id in by_id
        )
        if selected_chars > self._selected_context_max_chars:
            violations.append(
                _violation(
                    "context.selected_char_limit_exceeded",
                    "context.selected_turn_ids",
                    "Selected turn content exceeds the local character budget.",
                )
            )

        clarification_needed = (
            context.relation is ContextRelation.UNCLEAR
            or bool(context.unresolved_references)
        )
        if clarification_needed and not plan.clarification.required:
            violations.append(
                _violation(
                    "clarification.required_for_unclear_context",
                    "clarification.required",
                    "Unclear context requires a clarification plan.",
                )
            )
        if (
            clarification_needed
            and plan.execution.mode is not ExecutionMode.CLARIFY
        ):
            violations.append(
                _violation(
                    "execution.clarify_mode_required",
                    "execution.mode",
                    "Unclear context must use clarify execution mode.",
                )
            )

    @staticmethod
    def _validate_execution_and_facts(
        plan: UnifiedTurnPlan,
        violations: list[PlanViolation],
    ) -> None:
        mode = plan.execution.mode
        fact_check = plan.fact_check
        if (
            mode is ExecutionMode.EXECUTE_ASSET
            and plan.execution.primary_asset is None
        ):
            violations.append(
                _violation(
                    "execution.primary_asset_required",
                    "execution.primary_asset",
                    "Execute-asset mode requires a primary asset.",
                )
            )
        current_fact_mode = mode in {
            ExecutionMode.FACT_CHECK,
            ExecutionMode.COMPLEX_FACT,
        }
        if current_fact_mode and not fact_check.required:
            violations.append(
                _violation(
                    "fact_check.required",
                    "fact_check.required",
                    "Current-fact execution requires a fact-check plan.",
                )
            )
        if (
            current_fact_mode
            and fact_check.owner is not EvidenceOwner.PLANNER
        ):
            violations.append(
                _violation(
                    "fact_check.planner_owner_required",
                    "fact_check.owner",
                    "Current-fact execution must be owned by the planner.",
                )
            )
        if current_fact_mode and not fact_check.search_query.strip():
            violations.append(
                _violation(
                    "fact_check.search_query_required",
                    "fact_check.search_query",
                    "Current-fact execution requires a bounded search query.",
                )
            )
        if mode is ExecutionMode.RECIPE:
            primary = plan.execution.primary_asset
            if primary is None or primary.asset_type != "recipe":
                violations.append(
                    _violation(
                        "execution.recipe_asset_required",
                        "execution.primary_asset",
                        "Recipe mode requires a recipe primary asset.",
                    )
                )
            if fact_check.owner not in {
                EvidenceOwner.NONE,
                EvidenceOwner.ASSET,
            }:
                violations.append(
                    _violation(
                        "fact_check.recipe_owner_invalid",
                        "fact_check.owner",
                        "Recipe fact collection must be owned by the asset or none.",
                    )
                )

    @staticmethod
    def _validate_catalog_scope(
        plan: UnifiedTurnPlan,
        catalog: PlannerCatalog,
        *,
        violations: list[PlanViolation],
        rejected: list[PlanViolation],
        confirmation: list[PlanViolation],
    ) -> None:
        runtime_assets = {
            _asset_identity(asset): asset
            for asset in catalog.assets
            if asset.runtime_visible
        }
        primary = plan.execution.primary_asset
        allowed_identities = {
            (asset.asset_type, asset.name)
            for asset in plan.execution.allowed_assets
        }
        if primary is not None:
            primary_identity = (primary.asset_type, primary.name)
            if primary_identity not in allowed_identities:
                violations.append(
                    _violation(
                        "asset.primary_not_allowed",
                        "execution.primary_asset",
                        "Primary asset must also be present in allowed_assets.",
                    )
                )

        referenced = set(allowed_identities)
        if primary is not None:
            referenced.add((primary.asset_type, primary.name))
        allowed_skill = any(
            asset_type == "skill"
            for asset_type, _asset_name in allowed_identities
        )
        if (
            "execute_skill" in plan.execution.allowed_tools
            and not allowed_skill
        ):
            violations.append(
                _violation(
                    "asset.execute_skill_without_allowed_skill",
                    "execution.allowed_tools",
                    "execute_skill requires at least one allowed skill identity.",
                )
            )
        referenced.update(
            ("native_tool", name)
            for name in plan.execution.allowed_tools
            if name != "execute_skill"
        )
        unknown = [
            identity for identity in referenced if identity not in runtime_assets
        ]
        if unknown:
            violations.append(
                _violation(
                    "asset.unknown",
                    "execution.allowed_assets",
                    "All referenced assets and tools must exist in the runtime catalog.",
                )
            )

        known_referenced_assets = [
            runtime_assets[identity]
            for identity in referenced
            if identity in runtime_assets
        ]
        confirmation_assets = [
            asset
            for asset in known_referenced_assets
            if asset.side_effects or asset.requires_confirmation
        ]
        if confirmation_assets and not plan.execution.requires_confirmation:
            violations.append(
                _violation(
                    "asset.confirmation_flag_required",
                    "execution.requires_confirmation",
                    "Side-effecting assets require the confirmation flag.",
                )
            )
        elif confirmation_assets:
            confirmation.append(
                _violation(
                    "asset.confirmation_required",
                    "execution.allowed_assets",
                    "Side-effecting assets require explicit user confirmation.",
                )
            )

        if plan.execution.mode is not ExecutionMode.EXECUTE_ASSET or primary is None:
            return
        primary_catalog_asset = runtime_assets.get(
            (primary.asset_type, primary.name)
        )
        if primary_catalog_asset is None:
            return
        if not primary_catalog_asset.declared:
            rejected.append(
                _violation(
                    "asset.undeclared_direct_execution",
                    "execution.primary_asset",
                    "Undeclared assets cannot be executed directly.",
                )
            )
            return
        if (
            primary_catalog_asset.side_effects
            or primary_catalog_asset.requires_confirmation
        ):
            return
        if not primary_catalog_asset.read_only:
            rejected.append(
                _violation(
                    "asset.unsafe_direct_execution",
                    "execution.primary_asset",
                    "Direct execution requires an explicitly read-only asset.",
                )
            )
