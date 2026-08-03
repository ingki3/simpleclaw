"""Full-coverage exact Skill/Recipe를 mode 이전에 직접 실행한다."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from simpleclaw.agent.planner_catalog import PlannerAsset, PlannerCatalog
from simpleclaw.agent.resolution_ledger import (
    ResolutionLedger,
    attempt_signature,
)
from simpleclaw.agent.resolution_types import (
    AssetExecutionStatus,
    AssetResult,
    CapabilityCoverage,
    ComplexitySignal,
    ResolutionBudget,
)
from simpleclaw.agent.turn_plan import UnifiedTurnPlan

SkillExecutor = Callable[[str, str], Awaitable[object]]
RecipeExecutor = Callable[[str, dict[str, str]], Awaitable[object]]

ASSET_RESULT_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "schema": {"type": "string", "enum": ["asset_result.v1"]},
        "status": {
            "type": "string",
            "enum": [status.value for status in AssetExecutionStatus],
        },
        "data": {"type": "object"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "claim_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["claim_id", "claim_keys"],
            },
        },
        "resolved_claims": {"type": "array", "items": {"type": "string"}},
        "unresolved_claims": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "retryable": {"type": "boolean"},
        "side_effect": {"type": "boolean"},
        "effect_id": {"type": "string"},
        "tokens_used": {"type": "integer"},
    },
    "required": [
        "schema",
        "status",
        "data",
        "evidence",
        "resolved_claims",
        "unresolved_claims",
        "limitations",
        "retryable",
        "side_effect",
        "tokens_used",
    ],
    # Recipe-owned ``data``/``evidence`` payloads are domain-specific. Runtime
    # decode_asset_result performs the strict typed validation after generation.
}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def decode_asset_result(
    payload: object,
    *,
    asset_type: str,
    asset_name: str,
    side_effect: bool,
) -> AssetResult:
    """Strict ``asset_result.v1`` JSON envelope를 typed result로 변환한다."""
    data: Mapping[str, Any]
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("asset result must be strict JSON") from exc
        if not isinstance(decoded, Mapping):
            raise TypeError("asset result must be a JSON object")
        data = decoded
    elif isinstance(payload, Mapping):
        data = payload
    else:
        raise TypeError("asset result must be a mapping or JSON object")
    schema = str(data.get("schema") or data.get("contract") or data.get("version") or "")
    if schema != "asset_result.v1":
        raise ValueError("asset result contract must be asset_result.v1")
    try:
        status = AssetExecutionStatus(str(data.get("status") or ""))
    except ValueError as exc:
        raise ValueError("unsupported asset result status") from exc
    evidence_raw = data.get("evidence", ())
    evidence = tuple(
        dict(item)
        for item in evidence_raw
        if isinstance(item, Mapping)
    ) if isinstance(evidence_raw, list | tuple) else ()
    signals: list[ComplexitySignal] = []
    for item in data.get("complexity_signals", ()) if isinstance(data.get("complexity_signals", ()), list | tuple) else ():
        try:
            signal = ComplexitySignal(str(item))
        except ValueError:
            continue
        if signal not in signals:
            signals.append(signal)
    result_data = data.get("data")
    usage = data.get("usage")
    token_value = data.get("tokens_used", 0)
    if isinstance(usage, Mapping):
        token_value = usage.get("total_tokens", usage.get("tokens", token_value))
    tokens_used = (
        token_value
        if isinstance(token_value, int) and not isinstance(token_value, bool)
        else 0
    )
    reported_side_effect = data.get("side_effect")
    effect_unknown = not isinstance(reported_side_effect, bool)
    effect_reported = reported_side_effect is True or side_effect
    if effect_unknown or effect_reported:
        if status is AssetExecutionStatus.COMPLETED:
            status = AssetExecutionStatus.UNKNOWN_EFFECT
        resolved_claims = (
            ()
            if status in {
                AssetExecutionStatus.UNKNOWN_EFFECT,
                AssetExecutionStatus.PARTIAL_SUCCESS,
            }
            else _string_tuple(data.get("resolved_claims"))[:64]
        )
        unresolved_claims = _string_tuple(data.get("unresolved_claims"))
        if not unresolved_claims:
            unresolved_claims = _string_tuple(data.get("resolved_claims"))
        limitations = list(_string_tuple(data.get("limitations"))[:32])
        limitations.append(
            "side_effect_status_unknown"
            if effect_unknown
            else "reported_side_effect_unverified"
        )
    else:
        resolved_claims = _string_tuple(data.get("resolved_claims"))[:64]
        unresolved_claims = _string_tuple(data.get("unresolved_claims"))[:64]
        limitations = list(_string_tuple(data.get("limitations"))[:32])
    return AssetResult(
        asset_type=asset_type,
        asset_name=asset_name,
        status=status,
        data=dict(result_data) if isinstance(result_data, Mapping) else {},
        evidence=evidence[:64],
        resolved_claims=resolved_claims,
        unresolved_claims=unresolved_claims,
        next_questions=_string_tuple(data.get("next_questions"))[:16],
        complexity_signals=tuple(signals),
        side_effect=effect_unknown or effect_reported,
        effect_id=str(data.get("effect_id") or "")[:256],
        retryable=bool(data.get("retryable", False)),
        limitations=tuple(dict.fromkeys(limitations))[:32],
        tokens_used=max(0, tokens_used),
    )


class CapabilityExecutor:
    """Catalog identity를 재검증한 뒤 exact asset을 한 번만 실행한다."""

    def __init__(
        self,
        *,
        catalog: PlannerCatalog,
        execute_skill: SkillExecutor | None = None,
        execute_recipe: RecipeExecutor | None = None,
    ) -> None:
        self._catalog = catalog
        self._execute_skill = execute_skill
        self._execute_recipe = execute_recipe

    async def execute(
        self,
        plan: UnifiedTurnPlan,
        *,
        budget: ResolutionBudget,
        ledger: ResolutionLedger,
    ) -> AssetResult:
        capability = plan.capability
        asset_ref = capability.primary_asset
        if capability.coverage is not CapabilityCoverage.FULL or asset_ref is None:
            return AssetResult(
                asset_type="none",
                asset_name="",
                status=AssetExecutionStatus.UNSUPPORTED,
                limitations=("full_coverage_exact_asset_required",),
            )
        if plan.catalog_fingerprint != self._catalog.fingerprint:
            return AssetResult(
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                status=AssetExecutionStatus.DENIED,
                limitations=("catalog_fingerprint_mismatch",),
            )
        asset = self._find_asset(asset_ref.asset_type, asset_ref.name)
        if asset is None or not self._eligible(asset):
            return AssetResult(
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                status=AssetExecutionStatus.UNSUPPORTED,
                limitations=("asset_not_fast_path_eligible",),
            )
        snapshot = budget.snapshot(
            steps_used=ledger.steps_used,
            tool_calls_used=ledger.tool_calls_used,
            tokens_used=ledger.tokens_used,
        )
        if not snapshot.can_continue:
            return AssetResult(
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                status=AssetExecutionStatus.FAILED_TERMINAL,
                limitations=("budget_exhausted",),
            )
        question = plan.context.standalone_question
        signature = attempt_signature(
            question=question,
            asset_type=asset_ref.asset_type,
            asset_name=asset_ref.name,
            parameters={"contract": "query.v1"},
        )
        if not ledger.record_attempt(signature):
            return AssetResult(
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                status=AssetExecutionStatus.FAILED_TERMINAL,
                limitations=("repeated_attempt_signature",),
            )
        try:
            if asset_ref.asset_type == "skill" and self._execute_skill is not None:
                raw = await budget.wait_for(
                    self._execute_skill(asset_ref.name, question)
                )
            elif asset_ref.asset_type == "recipe" and self._execute_recipe is not None:
                raw = await budget.wait_for(
                    self._execute_recipe(
                        asset_ref.name,
                        {
                            "query": question,
                            "domain": plan.fact_check.domain,
                            "intents": json.dumps(
                                plan.fact_check.intents,
                                ensure_ascii=False,
                            ),
                            "reference_date": plan.fact_check.reference_date,
                            "required_claims": json.dumps(
                                plan.fact_check.required_claims,
                                ensure_ascii=False,
                            ),
                        },
                    )
                )
            else:
                raise ValueError("exact executor unavailable")
            result = decode_asset_result(
                raw,
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                side_effect=asset.side_effects,
            )
        except TimeoutError:
            result = AssetResult(
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                status=(
                    AssetExecutionStatus.UNKNOWN_EFFECT
                    if asset.side_effects
                    else AssetExecutionStatus.FAILED_TERMINAL
                ),
                side_effect=asset.side_effects,
                limitations=("deadline_exhausted",),
            )
        except Exception as exc:  # noqa: BLE001 - typed fail-closed boundary
            result = AssetResult(
                asset_type=asset_ref.asset_type,
                asset_name=asset_ref.name,
                status=(
                    AssetExecutionStatus.UNKNOWN_EFFECT
                    if asset.side_effects
                    else AssetExecutionStatus.FAILED_TERMINAL
                ),
                side_effect=asset.side_effects,
                limitations=(f"typed_asset_result_error:{type(exc).__name__}",),
            )
        ledger.record_usage(steps=1, tool_calls=1, tokens=result.tokens_used)
        ledger.append_asset_result(result)
        return result

    def _find_asset(self, asset_type: str, name: str) -> PlannerAsset | None:
        return next(
            (
                item
                for item in self._catalog.assets
                if item.asset_type == asset_type and item.name == name
            ),
            None,
        )

    @staticmethod
    def _eligible(asset: PlannerAsset) -> bool:
        typed = (
            asset.declared
            and asset.coverage == "full_coverage"
            and asset.input_contract == "query.v1"
            and asset.output_contract == "asset_result.v1"
        )
        # Direct exact execution은 evidence-only read path다. Confirmation이나
        # side effect가 필요한 자산은 별도 일반/confirmation 경로가 소유한다.
        safe = (
            asset.read_only
            and not asset.side_effects
            and not asset.requires_confirmation
        )
        return typed and safe
