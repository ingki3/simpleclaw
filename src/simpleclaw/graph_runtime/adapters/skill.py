"""Skill 소유 argument binding으로 정확한 SkillDefinition을 dispatch한다."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from simpleclaw.skills.models import SkillDefinition, SkillResult

from ..contracts import AssetInvocationV1, NormalizedAssetResultV1
from ..contracts_registry import (
    ContractRegistryError,
    ContractRegistrySnapshotV1,
)
from ..idempotency import RedispatchDecision, guard_redispatch
from ..status import AssetResultStatus, EffectStatus
from .base import AdapterResponse

SkillExecutor = Callable[
    [SkillDefinition, list[str]],
    Awaitable[Mapping[str, Any] | SkillResult],
]


class GenericSkillAdapter:
    """typed invocation을 검증하고 discovery된 정확한 Skill 하나만 호출한다."""

    def __init__(
        self,
        registry: ContractRegistrySnapshotV1,
        definition: SkillDefinition,
        executor: SkillExecutor | None = None,
    ) -> None:
        """registry snapshot과 exact definition을 고정해 drift를 탐지한다."""
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
        """검증·멱등성 gate를 통과한 invocation만 외부 executor에 전달한다."""
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
                dispatched=False,
                receipt_reused=(
                    guard.decision is RedispatchDecision.REUSE_RECEIPT
                ),
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
            argv = _bind_arguments(self._definition, canonical.payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _failure(
                invocation,
                "binding.invalid",
                dispatched=False,
            )
        try:
            if self._executor is None:
                from simpleclaw.skills.executor import execute_skill

                raw_result = await execute_skill(self._definition, argv)
            else:
                raw_result = await self._executor(self._definition, argv)
        # 주입된 executor는 외부 경계이므로 알 수 없는 provider/runtime 실패도
        # graph fallback으로 새지 않게 fail-closed 응답으로 정규화한다.
        except Exception:  # noqa: BLE001
            return _failure(
                invocation,
                "executor.failed",
                dispatched=True,
                effect_status=_failed_effect(entry.snapshot.side_effects),
            )
        try:
            output = _result_payload(raw_result)
            normalized = self._registry.validate_canonical(
                entry.output_descriptor, output
            )
        except RuntimeError:
            return _failure(
                invocation,
                "executor.failed",
                dispatched=True,
                effect_status=_failed_effect(entry.snapshot.side_effects),
            )
        except ContractRegistryError as exc:
            return _failure(
                invocation,
                exc.code,
                dispatched=True,
                effect_status=_failed_effect(entry.snapshot.side_effects),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return _failure(
                invocation,
                "output.invalid",
                dispatched=True,
                effect_status=_failed_effect(entry.snapshot.side_effects),
            )

        effect = (
            EffectStatus.VERIFIED
            if entry.snapshot.side_effects
            else EffectStatus.NONE
        )
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

    def _candidate(self, invocation: AssetInvocationV1):
        """invocation identity가 registry의 exact Skill과 일치할 때만 후보를 반환한다."""
        entry = self._registry.asset(invocation.asset_ref)
        if entry is None or invocation.asset_ref.type != "skill":
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


def _bind_arguments(
    definition: SkillDefinition,
    payload: Mapping[str, Any],
) -> list[str]:
    """Skill 소유 binding 선언만 사용해 opaque payload를 argv로 변환한다."""
    metadata = definition.argument_binding
    if metadata is None:
        raise ValueError("missing argument binding")
    binding = metadata.binding
    strategy = binding.get("strategy")
    order = binding.get("order")
    if strategy not in {"named", "positional", "json"}:
        raise ValueError("unsupported binding strategy")
    if strategy == "json":
        return [json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))]
    if not isinstance(order, list) or any(not isinstance(key, str) for key in order):
        raise ValueError("binding order must be a string list")
    if set(order) != set(payload):
        raise ValueError("binding order must cover the exact payload")
    argv: list[str] = []
    for key in order:
        value = _argv_value(payload[key])
        if strategy == "named":
            argv.extend((f"--{key.replace('_', '-')}", value))
        else:
            argv.append(value)
    return argv


def _argv_value(value: Any) -> str:
    """문자열 외 값은 결정적 JSON으로 직렬화해 argv identity를 보존한다."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _result_payload(raw_result: Mapping[str, Any] | SkillResult) -> Mapping[str, Any]:
    """성공한 executor 결과만 output contract 검증용 mapping으로 변환한다."""
    if isinstance(raw_result, Mapping):
        return raw_result
    if not raw_result.success:
        raise RuntimeError("skill executor returned failure")
    value = json.loads(raw_result.output)
    if not isinstance(value, dict):
        raise TypeError("skill output must be a JSON object")
    return value


def _failed_effect(side_effects: bool) -> EffectStatus:
    """부수효과 executor 실패 시 실제 반영 여부를 보수적으로 UNKNOWN 처리한다."""
    return EffectStatus.UNKNOWN if side_effects else EffectStatus.NONE


def _failure(
    invocation: AssetInvocationV1,
    code: str,
    *,
    dispatched: bool = False,
    effect_status: EffectStatus = EffectStatus.NONE,
) -> AdapterResponse:
    """실패 위치와 effect 불확실성을 graph가 재해석하지 않도록 정규화한다."""
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
