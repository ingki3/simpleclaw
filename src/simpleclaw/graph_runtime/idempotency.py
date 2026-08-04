"""Domain-neutral invocation signatures and external-effect redispatch guard."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .contracts import AssetBindingRefV1, AssetInvocationV1
from .status import EffectStatus


class ActionReceiptLike(Protocol):
    """Structural subset shared with the append-only graph action receipt."""

    invocation_id: str
    idempotency_key: str
    effect_status: EffectStatus


class RedispatchDecision(str, Enum):
    DISPATCH = "dispatch"
    REUSE_RECEIPT = "reuse_receipt"
    BLOCK_UNKNOWN = "block_unknown"
    BLOCK_EXISTING = "block_existing"


@dataclass(frozen=True)
class RedispatchGuardResult:
    decision: RedispatchDecision
    idempotency_key: str
    effect_status: EffectStatus
    receipt: ActionReceiptLike | None = None


def invocation_signature(
    invocation: AssetInvocationV1,
    binding_ref: AssetBindingRefV1,
) -> str:
    """Hash every immutable dispatch identity without inspecting payload keys."""
    canonical = json.dumps(
        {
            "invocation_id": invocation.invocation_id,
            "asset_ref": invocation.asset_ref.model_dump(),
            "definition_fingerprint": invocation.definition_fingerprint,
            "input_contract": invocation.input_contract.model_dump(),
            "payload_hash": invocation.payload_hash,
            "output_contract": invocation.output_contract.model_dump(),
            "binding_ref": binding_ref.model_dump(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def guard_redispatch(
    invocation: AssetInvocationV1,
    binding_ref: AssetBindingRefV1,
    *,
    receipts: tuple[object, ...] = (),
    dispatch_started: bool = False,
) -> RedispatchGuardResult:
    """Return a fail-closed decision before any external executor is called."""
    key = invocation_signature(invocation, binding_ref)
    matching = tuple(
        receipt
        for receipt in receipts
        if getattr(receipt, "invocation_id", None) == invocation.invocation_id
        and getattr(receipt, "idempotency_key", None) == key
        and isinstance(getattr(receipt, "effect_status", None), EffectStatus)
    )
    if len(matching) > 1:
        return RedispatchGuardResult(
            RedispatchDecision.BLOCK_EXISTING,
            key,
            EffectStatus.UNKNOWN,
        )
    if matching:
        receipt = matching[0]
        effect_status = receipt.effect_status
        decision = (
            RedispatchDecision.REUSE_RECEIPT
            if effect_status is EffectStatus.VERIFIED
            else RedispatchDecision.BLOCK_EXISTING
        )
        return RedispatchGuardResult(decision, key, effect_status, receipt)
    if dispatch_started:
        return RedispatchGuardResult(
            RedispatchDecision.BLOCK_UNKNOWN,
            key,
            EffectStatus.UNKNOWN,
        )
    return RedispatchGuardResult(
        RedispatchDecision.DISPATCH,
        key,
        EffectStatus.NONE,
    )
