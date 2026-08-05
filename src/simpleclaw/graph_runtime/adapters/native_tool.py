"""Native tool 소유 Function Calling 계약으로 typed invocation을 dispatch한다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from simpleclaw.agent.tool_schemas import NativeToolSpec

from ..contracts import AssetInvocationV1, NormalizedAssetResultV1
from ..contracts_registry import ContractRegistryError, ContractRegistrySnapshotV1
from ..status import AssetResultStatus, EffectStatus
from .base import AdapterResponse

NativeToolExecutor = Callable[
    [NativeToolSpec, Mapping[str, Any]], Awaitable[Mapping[str, Any] | str]
]


class GenericNativeToolAdapter:
    """Registry가 검증한 exact native tool 인자 객체만 executor로 전달한다."""

    def __init__(
        self,
        registry: ContractRegistrySnapshotV1,
        definition: NativeToolSpec,
        executor: NativeToolExecutor | None = None,
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
        del receipts, dispatch_started
        entry = self._registry.asset(invocation.asset_ref)
        if entry is None or invocation.asset_ref.type != "native_tool":
            return _failure(invocation, "definition.not_found")
        if self._definition.definition.name != invocation.asset_ref.name:
            return _failure(invocation, "definition.not_exact")
        if self._definition.definition_fingerprint != invocation.definition_fingerprint:
            return _failure(invocation, "definition.drift")
        binding = entry.snapshot.declared_binding
        if binding is None or self._definition.argument_binding is None:
            return _failure(invocation, "binding.missing")
        if self._definition.argument_binding.binding.get("strategy") != "arguments":
            return _failure(invocation, "binding.invalid")
        candidate = self._registry.dispatch_candidate(
            asset_ref=invocation.asset_ref,
            definition_fingerprint=invocation.definition_fingerprint,
            input_contract=invocation.input_contract,
            output_contract=invocation.output_contract,
            binding_ref=binding,
            registry_fingerprint=self._registry.fingerprint,
        )
        if candidate is None:
            return _failure(invocation, "definition.drift")
        try:
            canonical = self._registry.validate_canonical(
                entry.input_descriptor, invocation.payload
            )
        except ContractRegistryError as exc:
            return _failure(invocation, exc.code)
        if canonical.payload_hash != invocation.payload_hash:
            return _failure(invocation, "payload.hash_mismatch")
        if entry.snapshot.side_effects and not authorized:
            return AdapterResponse(
                invocation_id=invocation.invocation_id,
                status=AssetResultStatus.BLOCKED,
                input_payload_hash=canonical.payload_hash,
                effect_status=EffectStatus.CONFIRMATION_REQUIRED,
                error_code="effect.authorization_required",
            )
        if self._executor is None:
            return _failure(invocation, "executor.missing")
        try:
            raw = await self._executor(self._definition, canonical.payload)
            output: Mapping[str, Any] = (
                {"content": raw} if isinstance(raw, str) else raw
            )
            normalized = self._registry.validate_canonical(
                entry.output_descriptor, output
            )
        except ContractRegistryError as exc:
            return _failure(invocation, exc.code, dispatched=True)
        except Exception:  # noqa: BLE001 - injected native boundary is fail-closed
            return _failure(invocation, "executor.failed", dispatched=True)
        result = NormalizedAssetResultV1(
            invocation_id=invocation.invocation_id,
            output_contract=invocation.output_contract,
            status=AssetResultStatus.RESOLVED,
            payload=normalized.payload,
            payload_hash=normalized.payload_hash,
            effect_status=EffectStatus.NONE,
        )
        return AdapterResponse(
            invocation_id=invocation.invocation_id,
            status=AssetResultStatus.RESOLVED,
            input_payload_hash=canonical.payload_hash,
            effect_status=EffectStatus.NONE,
            result=result,
            dispatched=True,
        )


def _failure(
    invocation: AssetInvocationV1,
    error_code: str,
    *,
    dispatched: bool = False,
) -> AdapterResponse:
    return AdapterResponse(
        invocation_id=invocation.invocation_id,
        status=AssetResultStatus.FAILED,
        input_payload_hash=invocation.payload_hash,
        effect_status=EffectStatus.NONE,
        dispatched=dispatched,
        error_code=error_code,
    )
