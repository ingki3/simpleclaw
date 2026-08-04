from __future__ import annotations

from dataclasses import dataclass

import pytest

from simpleclaw.graph_runtime.contracts import (
    AssetBindingRefV1,
    AssetInvocationV1,
    AssetRefV1,
    ContractRefV1,
)
from simpleclaw.graph_runtime.idempotency import (
    RedispatchDecision,
    guard_redispatch,
    invocation_signature,
)
from simpleclaw.graph_runtime.status import EffectStatus


def _invocation(payload_hash: str = "payload-hash") -> AssetInvocationV1:
    owner = AssetRefV1(type="skill", name="opaque")
    return AssetInvocationV1(
        invocation_id="invoke-1",
        asset_ref=owner,
        definition_fingerprint="definition-hash",
        input_contract=ContractRefV1(
            contract_id="opaque.input",
            version="1",
            owner_ref=owner,
            schema_hash="input-hash",
        ),
        payload={"opaque": "value"},
        payload_hash=payload_hash,
        output_contract=ContractRefV1(
            contract_id="opaque.output",
            version="1",
            owner_ref=owner,
            schema_hash="output-hash",
        ),
    )


def _binding(binding_hash: str = "binding-hash") -> AssetBindingRefV1:
    return AssetBindingRefV1(
        owner_ref=AssetRefV1(type="skill", name="opaque"),
        binding_id="argv.v1",
        binding_hash=binding_hash,
    )


@dataclass(frozen=True)
class _Receipt:
    invocation_id: str
    idempotency_key: str
    effect_status: EffectStatus


def test_signature_is_deterministic_and_covers_payload_and_binding_hashes() -> None:
    first = invocation_signature(_invocation(), _binding())

    assert first == invocation_signature(_invocation(), _binding())
    assert first != invocation_signature(_invocation("changed"), _binding())
    assert first != invocation_signature(_invocation(), _binding("changed"))
    assert len(first) == 64


@pytest.mark.parametrize("status", [EffectStatus.UNKNOWN, EffectStatus.PARTIAL])
def test_unknown_or_partial_receipt_blocks_redispatch(status: EffectStatus) -> None:
    invocation = _invocation()
    binding = _binding()
    receipt = _Receipt(
        invocation_id=invocation.invocation_id,
        idempotency_key=invocation_signature(invocation, binding),
        effect_status=status,
    )

    guarded = guard_redispatch(invocation, binding, receipts=(receipt,))

    assert guarded.decision is RedispatchDecision.BLOCK_EXISTING
    assert guarded.effect_status is status


def test_unrelated_receipt_does_not_block_and_started_without_receipt_is_unknown() -> None:
    invocation = _invocation()
    binding = _binding()
    unrelated = _Receipt(
        invocation_id="other",
        idempotency_key="other-key",
        effect_status=EffectStatus.VERIFIED,
    )

    allowed = guard_redispatch(invocation, binding, receipts=(unrelated,))
    unknown = guard_redispatch(invocation, binding, dispatch_started=True)

    assert allowed.decision is RedispatchDecision.DISPATCH
    assert unknown.decision is RedispatchDecision.BLOCK_UNKNOWN
    assert unknown.effect_status is EffectStatus.UNKNOWN
