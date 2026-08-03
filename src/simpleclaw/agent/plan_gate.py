"""UnifiedTurnPlan을 실행 전에 검증하는 순수 로컬 gate.

PlanGate는 사용자 의도나 route를 다시 추론하지 않는다. planner가 반환한 구조를
현재 context 후보, immutable capability catalog, 고정 실행 정책과 대조하고
downstream이 repair/clarify/confirmation/reject 중 하나를 결정할 수 있는 안정적인
결과만 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.evidence_policy import approved_collectors_from_plan
from simpleclaw.agent.freshness_policy import freshness_is_required
from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.turn_plan import (
    AssetRef,
    CapabilityCoverage,
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


def _eligible_exact_asset(
    asset: PlannerAsset,
    *,
    domain: str,
    intents: frozenset[str],
) -> bool:
    """typed domain/intent를 모두 덮는 안전한 exact read-only asset만 고른다."""
    return (
        asset.runtime_visible
        and asset.declared
        and asset.coverage == "full_coverage"
        and asset.input_contract == "query.v1"
        and asset.output_contract == "asset_result.v1"
        and asset.read_only
        and not asset.side_effects
        and not asset.requires_confirmation
        and domain in asset.domains
        and bool(intents)
        and intents.issubset(asset.intents)
    )


def _canonical_sports_result_claims(
    claims: tuple[str, ...],
    *,
    intents: frozenset[str],
) -> tuple[str, ...]:
    """종료 경기 claim을 provider의 bounded typed key로만 정규화한다.

    exact sports recipe가 지원하는 completed-result 경계에서만 적용한다. 알려진
    score/winner/game-result 표현은 canonical key로 축소하고, attendance처럼 알 수
    없는 표현은 원문 claim을 그대로 남겨 downstream validator가 fail-closed한다.
    """
    if not intents.intersection({"current_result", "completed_result", "live_score"}):
        return claims

    aliases = {
        "score": (
            "score",
            "scores",
            "finalscore",
            "gamescore",
            "점수",
            "스코어",
        ),
        "winner": (
            "winner",
            "winners",
            "winningteam",
            "승리팀",
            "승자",
            "승패",
        ),
        "game_result": (
            "gameresult",
            "gameresults",
            "finalresult",
            "경기결과",
            "최종결과",
        ),
    }
    normalized: list[str] = []
    for claim in claims:
        compact = "".join(char for char in claim.casefold() if char.isalnum())
        keys = [
            key
            for key in ("game_result", "score", "winner")
            if any(alias in compact for alias in aliases[key])
        ]
        normalized.extend(keys or [claim])
    return tuple(dict.fromkeys(normalized))


def _repair_unscoped_evidence_plan(
    plan: UnifiedTurnPlan,
    *,
    catalog: PlannerCatalog,
) -> tuple[UnifiedTurnPlan, bool]:
    """asset-0 evidence plan을 유일한 typed exact asset으로만 좁힌다.

    두 후보 이상이거나 후보가 없으면 원래 plan을 돌려주고 caller가 REPAIR로
    fail-closed한다. 사용자 원문이나 keyword는 이 경계에서 읽지 않는다.
    """
    if not (
        plan.execution.mode
        in {
            ExecutionMode.DIRECT_ANSWER,
            ExecutionMode.ANSWER_WITH_EVIDENCE,
        }
        and plan.fact_check.required
        and plan.capability.primary_asset is None
        and not plan.capability.supporting_assets
        and not plan.execution.allowed_assets
        and not plan.execution.allowed_tools
    ):
        return plan, False
    domain = plan.fact_check.domain.strip().lower()
    intents = frozenset(
        intent.strip().lower()
        for intent in (plan.fact_check.intents or plan.intents)
        if intent.strip()
    )
    candidates = [
        asset
        for asset in catalog.assets
        if _eligible_exact_asset(asset, domain=domain, intents=intents)
    ]
    if len(candidates) != 1:
        return plan, True
    selected = candidates[0]
    selected_ref = AssetRef(selected.asset_type, selected.name)
    required_claims = plan.fact_check.required_claims
    if _asset_identity(selected) == ("recipe", "sports-live") and domain == "sports":
        required_claims = _canonical_sports_result_claims(
            required_claims,
            intents=intents,
        )
    return (
        replace(
            plan,
            capability=replace(
                plan.capability,
                coverage=CapabilityCoverage.FULL,
                primary_asset=selected_ref,
                supporting_assets=(),
                fallback_modes=tuple(
                    ExecutionMode(mode)
                    for mode in selected.fallback_modes
                    if mode in {item.value for item in ExecutionMode}
                ),
                reason="unique_typed_catalog_repair",
            ),
            execution=replace(
                plan.execution,
                primary_asset=selected_ref,
                allowed_assets=(),
                allowed_tools=(),
                reason="unique_typed_catalog_repair",
            ),
            fact_check=replace(
                plan.fact_check,
                owner=EvidenceOwner.ASSET,
                required_claims=required_claims,
            ),
        ),
        False,
    )


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

        plan, unresolved_asset_scope = _repair_unscoped_evidence_plan(
            plan,
            catalog=catalog,
        )
        if unresolved_asset_scope:
            violations.append(
                _violation(
                    "fact_check.exact_asset_not_unique",
                    "capability.primary_asset",
                    "Unscoped required evidence needs one unique typed exact asset.",
                )
            )

        if plan.catalog_fingerprint != catalog.fingerprint:
            violations.append(
                _violation(
                    "catalog.fingerprint_mismatch",
                    "catalog_fingerprint",
                    "Plan catalog snapshot does not match the runtime catalog.",
                )
            )

        self._validate_context(plan, candidates, violations)
        self._validate_execution_and_facts(plan, catalog, violations)
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
                violations=(*violations, *rejected),
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
        catalog: PlannerCatalog,
        violations: list[PlanViolation],
    ) -> None:
        mode = plan.execution.mode
        fact_check = plan.fact_check
        approved_collectors = approved_collectors_from_plan(plan, catalog=catalog)
        has_applicable_collector = (
            bool(approved_collectors - {"web_fetch"})
            or (
                "web_fetch" in approved_collectors
                and fact_check.search_query.startswith(("http://", "https://"))
            )
        )
        if (
            plan.capability.coverage is CapabilityCoverage.FULL
            and plan.capability.primary_asset is None
        ):
            violations.append(
                _violation(
                    "execution.primary_asset_required",
                    "execution.primary_asset",
                    "Full coverage requires a primary asset.",
                )
            )
        current_fact_mode = (
            plan.capability.coverage is not CapabilityCoverage.FULL
            and mode
            in {
                ExecutionMode.ANSWER_WITH_EVIDENCE,
                ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            }
        )
        if current_fact_mode and not fact_check.required:
            violations.append(
                _violation(
                    "fact_check.required",
                    "fact_check.required",
                    "Current-fact execution requires a fact-check plan.",
                )
            )
        if fact_check.required and (
            not plan.domains or fact_check.domain not in plan.domains
        ):
            violations.append(
                _violation(
                    "fact_check.domain_mismatch",
                    "fact_check.domain",
                    "Fact domain must be present in the top-level plan domains.",
                )
            )
        if fact_check.required and (
            not fact_check.intents
            or not set(fact_check.intents).issubset(plan.intents)
        ):
            violations.append(
                _violation(
                    "fact_check.intent_mismatch",
                    "fact_check.intents",
                    "Fact intents must be a non-empty subset of plan intents.",
                )
            )
        if (
            fact_check.required
            and "current_result" in fact_check.intents
            and not fact_check.reference_date
        ):
            violations.append(
                _violation(
                    "fact_check.reference_date_required",
                    "fact_check.reference_date",
                    "Current-result verification requires a resolved reference date.",
                )
            )
        if current_fact_mode and not fact_check.required_claims:
            violations.append(
                _violation(
                    "fact_check.required_claims",
                    "fact_check.required_claims",
                    "Current-fact execution requires explicit claims to verify.",
                )
            )
        if (
            fact_check.required
            and fact_check.owner is EvidenceOwner.NONE
        ):
            violations.append(
                _violation(
                    "fact_check.owner_required",
                    "fact_check.owner",
                    "Required evidence must have a planner or asset owner.",
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
        if (
            fact_check.required
            and fact_check.owner is EvidenceOwner.PLANNER
            and mode
            not in {
                ExecutionMode.ANSWER_WITH_EVIDENCE,
                ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            }
        ):
            violations.append(
                _violation(
                    "fact_check.evidence_capable_mode_required",
                    "execution.mode",
                    "Planner-owned required evidence needs an evidence-capable execution mode.",
                )
            )
        if (
            fact_check.required
            and fact_check.owner is EvidenceOwner.PLANNER
            and not fact_check.search_query.strip()
        ):
            violations.append(
                _violation(
                    "fact_check.search_query_required",
                    "fact_check.search_query",
                    "Planner-owned required evidence needs a bounded search query.",
                )
            )
        if (
            fact_check.required
            and fact_check.owner is EvidenceOwner.PLANNER
            and mode
            in {
                ExecutionMode.ANSWER_WITH_EVIDENCE,
                ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
            }
            and not has_applicable_collector
        ):
            violations.append(
                _violation(
                    "fact_check.collector_required",
                    "execution.allowed_tools",
                    "Planner-owned required evidence needs an allowed collector.",
                )
            )
        if (
            mode is ExecutionMode.RESOLVE_COMPLEX_PROBLEM
            and not plan.execution.complexity_signals
        ):
            violations.append(
                _violation(
                    "execution.complexity_signal_required",
                    "execution.complexity_signals",
                    "Complex problem mode requires an explicit complexity signal.",
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
        primary = plan.capability.primary_asset
        allowed_identities = {
            (asset.asset_type, asset.name)
            for asset in plan.capability.supporting_assets
        }

        referenced = set(allowed_identities)
        if primary is not None:
            referenced.add((primary.asset_type, primary.name))
        allowed_skill = any(
            asset_type == "skill"
            for asset_type, _asset_name in allowed_identities
        ) or (primary is not None and primary.asset_type == "skill")
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
        if (
            freshness_is_required(plan, assets=known_referenced_assets)
            and not plan.fact_check.freshness_required
        ):
            violations.append(
                _violation(
                    "fact_check.freshness_required",
                    "fact_check.freshness_required",
                    "Current or freshness-sensitive facts require freshness validation.",
                )
            )
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

        if (
            plan.capability.coverage is not CapabilityCoverage.FULL
            or primary is None
        ):
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
            primary_catalog_asset.coverage != "full_coverage"
            or primary_catalog_asset.input_contract != "query.v1"
            or primary_catalog_asset.output_contract != "asset_result.v1"
        ):
            rejected.append(
                _violation(
                    "asset.typed_fast_path_contract_required",
                    "capability.primary_asset",
                    "Full coverage requires query.v1 and asset_result.v1 contracts.",
                )
            )
            return
        if primary.asset_type == "recipe" and plan.execution.allowed_tools:
            violations.append(
                _violation(
                    "asset.full_coverage_recipe_has_top_level_tools",
                    "execution.allowed_tools",
                    "Full-coverage recipes cannot retain top-level tool scope.",
                )
            )
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
