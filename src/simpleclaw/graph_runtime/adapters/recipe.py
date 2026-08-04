"""Exact RecipeDefinition dispatch through Recipe-owned deterministic mapping."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from simpleclaw.recipes.models import RecipeDefinition, RecipeResult

from ..contracts import AssetInvocationV1, AssetRefV1, NormalizedAssetResultV1
from ..contracts_registry import ContractRegistryError, ContractRegistrySnapshotV1
from ..idempotency import RedispatchDecision, guard_redispatch
from ..status import AssetResultStatus, EffectStatus
from .base import AdapterResponse, BoundSkillPayload

RecipeExecutor = Callable[
    [RecipeDefinition, tuple[BoundSkillPayload, ...]],
    Awaitable[Mapping[str, Any] | RecipeResult],
]


class GenericRecipeAdapter:
    """Validate one exact Recipe and expose only its declared step mappings."""

    def __init__(
        self,
        registry: ContractRegistrySnapshotV1,
        definition: RecipeDefinition,
        executor: RecipeExecutor | None = None,
    ) -> None:
        self._registry = registry
        self._definition = definition
        self._executor = executor

    async def dispatch(
        self,
        invocation: AssetInvocationV1,
        *,
        receipts: tuple[object, ...] = (),
        dispatch_started: bool = False,
        authorized: bool = False,
    ) -> AdapterResponse:
        entry, error = self._candidate(invocation)
        if error is not None:
            return _failure(invocation, error)
        assert entry is not None
        binding_ref = entry.snapshot.declared_binding
        assert binding_ref is not None
        try:
            canonical = self._registry.validate_canonical(
                entry.input_descriptor, invocation.payload
            )
        except ContractRegistryError as exc:
            return _failure(invocation, exc.code)
        if canonical.payload_hash != invocation.payload_hash:
            return _failure(invocation, "payload.hash_mismatch")

        guard = guard_redispatch(
            invocation,
            binding_ref,
            receipts=receipts,
            dispatch_started=dispatch_started,
        )
        if guard.decision is not RedispatchDecision.DISPATCH:
            return AdapterResponse(
                invocation_id=invocation.invocation_id,
                status=(
                    AssetResultStatus.RESOLVED
                    if guard.decision is RedispatchDecision.REUSE_RECEIPT
                    else AssetResultStatus.BLOCKED
                ),
                input_payload_hash=canonical.payload_hash,
                effect_status=guard.effect_status,
                receipt_reused=guard.decision is RedispatchDecision.REUSE_RECEIPT,
                error_code=(
                    None
                    if guard.decision is RedispatchDecision.REUSE_RECEIPT
                    else "effect.redispatch_blocked"
                ),
            )

        if entry.snapshot.side_effects and not authorized:
            return AdapterResponse(
                invocation_id=invocation.invocation_id,
                status=AssetResultStatus.BLOCKED,
                input_payload_hash=canonical.payload_hash,
                effect_status=EffectStatus.CONFIRMATION_REQUIRED,
                error_code="effect.authorization_required",
            )

        try:
            bound_steps = self.bind_skill_payloads(
                canonical.payload,
                canonical.payload_hash,
                canonical.payload_json,
            )
        except ContractRegistryError as exc:
            return _failure(invocation, exc.code)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _failure(invocation, "binding.invalid")
        try:
            if self._executor is None:
                from simpleclaw.recipes.executor import execute_recipe

                variables = {
                    key: _variable_value(value)
                    for key, value in canonical.payload.items()
                }
                raw_result = await execute_recipe(
                    self._definition,
                    variables=variables,
                    timeout=self._definition.settings.timeout,
                )
            else:
                raw_result = await self._executor(self._definition, bound_steps)
        # The injected executor is an external boundary. Unknown provider/runtime
        # failures must become a fail-closed adapter response, never escape to a
        # graph fallback path.
        except Exception:  # noqa: BLE001
            return _failure(
                invocation,
                "executor.failed",
                dispatched=True,
                effect_status=(
                    EffectStatus.UNKNOWN
                    if entry.snapshot.side_effects
                    else EffectStatus.NONE
                ),
            )
        try:
            output = _result_payload(raw_result)
            normalized = self._registry.validate_canonical(entry.output_descriptor, output)
        except ContractRegistryError as exc:
            return _failure(
                invocation,
                exc.code,
                dispatched=True,
                effect_status=(
                    EffectStatus.UNKNOWN
                    if entry.snapshot.side_effects
                    else EffectStatus.NONE
                ),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return _failure(
                invocation,
                "output.invalid",
                dispatched=True,
                effect_status=(
                    EffectStatus.UNKNOWN
                    if entry.snapshot.side_effects
                    else EffectStatus.NONE
                ),
            )

        effect = EffectStatus.VERIFIED if entry.snapshot.side_effects else EffectStatus.NONE
        result = NormalizedAssetResultV1(
            invocation_id=invocation.invocation_id,
            output_contract=invocation.output_contract,
            status=AssetResultStatus.RESOLVED,
            payload=normalized.payload,
            payload_hash=normalized.payload_hash,
            effect_status=effect,
        )
        return AdapterResponse(
            invocation_id=invocation.invocation_id,
            status=AssetResultStatus.RESOLVED,
            input_payload_hash=canonical.payload_hash,
            effect_status=effect,
            result=result,
            dispatched=True,
        )

    def bind_skill_payloads(
        self,
        payload: Mapping[str, Any],
        source_payload_hash: str,
        source_payload_json: str,
    ) -> tuple[BoundSkillPayload, ...]:
        """Apply declared source-to-target maps without semantic reinterpretation."""
        bound: list[BoundSkillPayload] = []
        for metadata in self._definition.step_bindings:
            binding = metadata.binding
            target = binding.get("target_skill")
            mapping = binding.get("map")
            if not isinstance(target, str) or not target:
                raise ValueError("target_skill must be a non-empty string")
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("map must be a non-empty object")
            target_asset = AssetRefV1(type="skill", name=target)
            target_entry = self._registry.asset(target_asset)
            if target_entry is None:
                raise ContractRegistryError("binding.target_not_found")
            child: dict[str, Any] = {}
            for source, destination in mapping.items():
                if not isinstance(source, str) or not isinstance(destination, str):
                    raise TypeError("map entries must be strings")
                if source not in payload or destination in child:
                    raise ValueError("mapping source missing or destination duplicated")
                child[destination] = payload[source]
            validated = self._registry.validate_canonical(
                target_entry.input_descriptor,
                child,
            )
            bound.append(
                BoundSkillPayload(
                    binding_id=metadata.binding_id,
                    target_skill=target,
                    target_asset=target_asset,
                    target_definition_fingerprint=(
                        target_entry.snapshot.definition_fingerprint
                    ),
                    input_contract=target_entry.input_descriptor.ref,
                    payload_json=validated.payload_json,
                    payload_hash=validated.payload_hash,
                    source_payload_json=source_payload_json,
                    source_payload_hash=source_payload_hash,
                )
            )
        return tuple(bound)

    def _candidate(self, invocation: AssetInvocationV1):
        entry = self._registry.asset(invocation.asset_ref)
        if entry is None or invocation.asset_ref.type != "recipe":
            return None, "definition.not_found"
        if self._definition.name != invocation.asset_ref.name:
            return None, "definition.not_exact"
        if self._definition.definition_fingerprint != invocation.definition_fingerprint:
            return None, "definition.drift"
        binding = entry.snapshot.declared_binding
        if binding is None:
            return None, "binding.missing"
        candidate = self._registry.dispatch_candidate(
            asset_ref=invocation.asset_ref,
            definition_fingerprint=invocation.definition_fingerprint,
            input_contract=invocation.input_contract,
            output_contract=invocation.output_contract,
            binding_ref=binding,
            registry_fingerprint=self._registry.fingerprint,
        )
        return (candidate, None) if candidate is not None else (None, "definition.drift")


def _result_payload(raw_result: Mapping[str, Any] | RecipeResult) -> Mapping[str, Any]:
    if isinstance(raw_result, Mapping):
        return raw_result
    if not raw_result.success:
        raise RuntimeError("recipe executor returned failure")
    if len(raw_result.step_results) != 1:
        raise ValueError("typed recipe output must be one JSON object")
    value = json.loads(raw_result.step_results[0].output)
    if not isinstance(value, dict):
        raise TypeError("recipe output must be a JSON object")
    return value


def _variable_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _failure(
    invocation: AssetInvocationV1,
    code: str,
    *,
    dispatched: bool = False,
    effect_status: EffectStatus = EffectStatus.NONE,
) -> AdapterResponse:
    return AdapterResponse(
        invocation_id=invocation.invocation_id,
        status=(
            AssetResultStatus.BLOCKED
            if effect_status in {EffectStatus.UNKNOWN, EffectStatus.PARTIAL}
            else AssetResultStatus.FAILED
        ),
        input_payload_hash=invocation.payload_hash,
        effect_status=effect_status,
        dispatched=dispatched,
        error_code=code,
    )
