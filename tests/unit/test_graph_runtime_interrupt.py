from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from simpleclaw.graph_runtime.checkpoint import (
    CheckpointContractError,
    InterruptRequestV1,
    UserDecisionV1,
    validate_resume,
)

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _request(kind: str = "clarification") -> InterruptRequestV1:
    common = {
        "interrupt_id": "interrupt-1",
        "kind": kind,
        "question": "계속할까요?",
        "resume_node": "react",
        "checkpoint_thread_id": "turn:1",
        "checkpoint_version": 3,
        "contract_version": "1",
        "contract_schema_hash": "contract-hash",
        "catalog_fingerprint": "catalog-hash",
        "plan_id": "plan-1",
        "plan_revision": 2,
        "expires_at": NOW + timedelta(minutes=5),
    }
    if kind == "confirmation":
        common.update(
            invocation_id="invoke-1",
            payload_hash="payload-hash",
            definition_fingerprint="definition-hash",
        )
    return InterruptRequestV1(**common)


def _decision(request: InterruptRequestV1, **update) -> UserDecisionV1:
    raw = {
        "interrupt_id": request.interrupt_id,
        "text": "서울 기준",
        "checkpoint_thread_id": request.checkpoint_thread_id,
        "checkpoint_version": request.checkpoint_version,
        "contract_version": request.contract_version,
        "contract_schema_hash": request.contract_schema_hash,
        "catalog_fingerprint": request.catalog_fingerprint,
        "plan_id": request.plan_id,
        "plan_revision": request.plan_revision,
        "invocation_id": request.invocation_id,
        "payload_hash": request.payload_hash,
        "definition_fingerprint": request.definition_fingerprint,
    }
    raw.update(update)
    return UserDecisionV1(**raw)


def test_clarification_creates_exactly_one_new_plan_revision() -> None:
    request = _request()
    control = validate_resume(request, _decision(request), now=NOW)

    assert control.previous_revision == 2
    assert control.next_revision == 3
    assert control.resume_node == "react"
    assert control.authorized is None


def test_confirmation_preserves_plan_revision_and_payload_hash() -> None:
    request = _request("confirmation")
    decision = _decision(request, text=None, confirmed=True)
    control = validate_resume(request, decision, now=NOW)

    assert control.previous_revision == control.next_revision == 2
    assert control.payload_hash == "payload-hash"
    assert control.authorized is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("checkpoint_version", 4),
        ("contract_schema_hash", "changed"),
        ("catalog_fingerprint", "changed"),
        ("payload_hash", "changed"),
        ("definition_fingerprint", "changed"),
    ],
)
def test_stale_confirmation_fails_closed(field, value) -> None:
    request = _request("confirmation")
    decision = _decision(request, text=None, confirmed=True, **{field: value})

    with pytest.raises(CheckpointContractError):
        validate_resume(request, decision, now=NOW)


def test_expired_interrupt_fails_closed() -> None:
    request = _request()
    with pytest.raises(CheckpointContractError, match="expired"):
        validate_resume(request, _decision(request), now=NOW + timedelta(hours=1))
