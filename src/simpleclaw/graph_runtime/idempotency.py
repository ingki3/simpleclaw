"""도메인 중립 invocation signature와 외부 effect redispatch guard를 정의한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .contracts import AssetBindingRefV1, AssetInvocationV1
from .status import EffectStatus


class IdempotencyInvariantError(ValueError):
    """동일 identity가 서로 다른 canonical payload를 가리킬 때 발생한다."""


def _stable_id(namespace: str, *parts: str) -> str:
    canonical = json.dumps(
        [namespace, *parts], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def delivery_id(request_id: str, artifact_hash: str, destination_ref: str) -> str:
    """같은 final artifact와 destination의 전송 identity를 고정한다."""
    return _stable_id("delivery.v1", request_id, artifact_hash, destination_ref)


def persistence_id(session_key: str, request_id: str, artifact_hash: str) -> str:
    """ConversationStore outbound write identity를 delivery와 분리한다."""
    return _stable_id("persistence.v1", session_key, request_id, artifact_hash)


class UniquePayloadLedger:
    """identity unique + same bytes no-op 규칙을 구현하는 journal primitive다."""

    def __init__(self) -> None:
        self._payload_hashes: dict[str, str] = {}

    def record(self, identity: str, payload_hash: str) -> bool:
        existing = self._payload_hashes.get(identity)
        if existing is None:
            self._payload_hashes[identity] = payload_hash
            return True
        if existing != payload_hash:
            raise IdempotencyInvariantError(
                f"identity {identity!r} already has a different payload"
            )
        return False

    def get(self, identity: str) -> str | None:
        return self._payload_hashes.get(identity)


class ActionReceiptLike(Protocol):
    """append-only graph action receipt와 공유하는 structural subset."""

    invocation_id: str
    idempotency_key: str
    effect_status: EffectStatus


class RedispatchDecision(str, Enum):
    """receipt와 dispatch 상태로 결정한 재호출 허용 여부."""

    DISPATCH = "dispatch"
    REUSE_RECEIPT = "reuse_receipt"
    BLOCK_UNKNOWN = "block_unknown"
    BLOCK_EXISTING = "block_existing"


@dataclass(frozen=True)
class RedispatchGuardResult:
    """외부 호출 전에 확정한 멱등성 key와 effect 상태."""

    decision: RedispatchDecision
    idempotency_key: str
    effect_status: EffectStatus
    receipt: ActionReceiptLike | None = None


def invocation_signature(
    invocation: AssetInvocationV1,
    binding_ref: AssetBindingRefV1,
) -> str:
    """payload key를 읽지 않고 모든 불변 dispatch identity를 hash한다."""
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
    """외부 executor 호출 전에 fail-closed 재호출 결정을 반환한다."""
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
