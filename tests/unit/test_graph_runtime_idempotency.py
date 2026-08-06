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
    IdempotencyInvariantError,
    RedispatchDecision,
    UniquePayloadLedger,
    canonical_artifact_content_hash,
    canonical_artifact_id,
    delivery_id,
    guard_redispatch,
    invocation_signature,
    persistence_id,
    validate_canonical_artifact_identity,
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


def test_delivery_and_persistence_ids_are_deterministic_and_separate() -> None:
    first_delivery = delivery_id("request-1", "artifact-hash", "chat-1")
    first_persistence = persistence_id(
        "session-1", "request-1", "artifact-hash"
    )

    assert first_delivery == delivery_id("request-1", "artifact-hash", "chat-1")
    assert first_persistence == persistence_id(
        "session-1", "request-1", "artifact-hash"
    )
    assert first_delivery != first_persistence


def test_canonical_artifact_identity_is_deterministic_and_fully_bound() -> None:
    request_id = "request-1"
    content = "final answer"
    artifact_id = canonical_artifact_id(request_id, content)
    content_hash = canonical_artifact_content_hash(content)

    validate_canonical_artifact_identity(
        request_id=request_id,
        content=content,
        artifact_id=artifact_id,
        content_hash=content_hash,
    )
    assert artifact_id == canonical_artifact_id(request_id, content)
    assert artifact_id != canonical_artifact_id("stale-request", content)
    assert artifact_id != canonical_artifact_id(request_id, "stale answer")


@pytest.mark.parametrize(
    ("artifact_id", "content_hash", "error"),
    [
        ("arbitrary-artifact", None, "identity mismatch"),
        (None, "stale-content-hash", "content hash mismatch"),
    ],
)
def test_canonical_artifact_identity_rejects_mismatch(
    artifact_id: str | None,
    content_hash: str | None,
    error: str,
) -> None:
    request_id = "request-1"
    content = "final answer"

    with pytest.raises(IdempotencyInvariantError, match=error):
        validate_canonical_artifact_identity(
            request_id=request_id,
            content=content,
            artifact_id=artifact_id
            or canonical_artifact_id(request_id, content),
            content_hash=content_hash
            or canonical_artifact_content_hash(content),
        )


def test_unique_payload_ledger_noops_same_payload_and_rejects_conflict() -> None:
    ledger = UniquePayloadLedger()

    assert ledger.record("persistence-1", "hash-1") is True
    assert ledger.record("persistence-1", "hash-1") is False
    with pytest.raises(IdempotencyInvariantError, match="different payload"):
        ledger.record("persistence-1", "hash-2")
