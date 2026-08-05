from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

from simpleclaw.capability import (
    CapabilityMetadata,
    parse_owned_binding_metadata,
    parse_owned_contract_metadata,
)
from simpleclaw.graph_runtime.adapters.skill import GenericSkillAdapter
from simpleclaw.graph_runtime.contracts import AssetInvocationV1
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.idempotency import invocation_signature
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus
from simpleclaw.skills.models import SkillDefinition, SkillResult


def _contract(name: str):
    value = parse_owned_contract_metadata(
        {
            "contract_id": f"fixture.{name}",
            "version": "1",
            "owner_ref": {"type": "skill", "name": "fixture-skill"},
            "json_schema": {
                "type": "object",
                "properties": {name: {"type": "string"}},
                "required": [name],
                "additionalProperties": False,
            },
        },
        source=name,
    )
    assert value is not None
    return value


def _definition() -> SkillDefinition:
    binding = parse_owned_binding_metadata(
        {
            "binding_id": "argv.v1",
            "owner_ref": {"type": "skill", "name": "fixture-skill"},
            "binding": {"strategy": "named", "order": ["nebula_key"]},
        },
        source="binding",
    )
    assert binding is not None
    return SkillDefinition(
        name="fixture-skill",
        capability=CapabilityMetadata(declared=True, read_only=True, side_effects=False),
        input_contract=_contract("nebula_key"),
        output_contract=_contract("result_token"),
        argument_binding=binding,
    )


def _invocation(skill: SkillDefinition, payload: dict[str, str]) -> AssetInvocationV1:
    registry = build_contract_registry([skill])
    entry = registry.entries[0]
    canonical = registry.validate_canonical(entry.input_descriptor, payload)
    return AssetInvocationV1(
        invocation_id="invoke-1",
        asset_ref=entry.snapshot.asset_ref,
        definition_fingerprint=skill.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )


@pytest.mark.asyncio
async def test_exact_definition_binding_and_hash_continuity() -> None:
    skill = _definition()
    registry = build_contract_registry([skill])
    calls = []

    async def executor(exact_definition, argv):
        calls.append((exact_definition, argv))
        return {"result_token": "ok"}

    invocation = _invocation(skill, {"nebula_key": "별빛"})
    response = await GenericSkillAdapter(registry, skill, executor).dispatch(invocation)

    assert calls == [(skill, ["--nebula-key", "별빛"])]
    assert response.status is AssetResultStatus.RESOLVED
    assert response.result is not None
    assert response.result.payload == {"result_token": "ok"}
    assert response.input_payload_hash == invocation.payload_hash
    expected = json.dumps(
        {"result_token": "ok"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert response.result.payload_hash == hashlib.sha256(expected.encode()).hexdigest()


@pytest.mark.asyncio
async def test_shell_binding_matches_execute_skill_args_contract() -> None:
    skill = _definition()
    binding = parse_owned_binding_metadata(
        {
            "binding_id": "shell-argv.v1",
            "owner_ref": {"type": "skill", "name": "fixture-skill"},
            "binding": {"strategy": "shell", "order": ["args"]},
        },
        source="binding",
    )
    assert binding is not None
    skill.input_contract = _contract("args")
    skill.argument_binding = binding
    registry = build_contract_registry([skill])
    calls = []

    async def executor(_definition, argv):
        calls.append(argv)
        return {"result_token": "ok"}

    invocation = _invocation(
        skill,
        {"args": '--query "US market close" --lookback-days 2 --format json'},
    )
    response = await GenericSkillAdapter(registry, skill, executor).dispatch(invocation)

    assert calls == [
        ["--query", "US market close", "--lookback-days", "2", "--format", "json"]
    ]
    assert response.status is AssetResultStatus.RESOLVED


@pytest.mark.asyncio
async def test_unknown_key_and_definition_drift_dispatch_zero() -> None:
    skill = _definition()
    registry = build_contract_registry([skill])
    calls = 0

    async def executor(_definition, _argv):
        nonlocal calls
        calls += 1
        return {"result_token": "never"}

    invocation = _invocation(skill, {"nebula_key": "value"})
    invalid = invocation.model_copy(
        update={"payload_json": json.dumps({"nebula_key": "value", "unknown": "x"})}
    )
    first = await GenericSkillAdapter(registry, skill, executor).dispatch(invalid)
    skill.description = "drifted after snapshot"
    second = await GenericSkillAdapter(registry, skill, executor).dispatch(invocation)

    assert calls == 0
    assert first.error_code is not None
    assert first.error_code.startswith("payload.unknown_key")
    assert second.error_code == "definition.drift"


@dataclass(frozen=True)
class _Receipt:
    invocation_id: str
    idempotency_key: str
    effect_status: EffectStatus


@pytest.mark.asyncio
async def test_receipt_resume_and_unknown_effect_never_redispatch() -> None:
    skill = _definition()
    skill.capability = CapabilityMetadata(
        declared=True,
        read_only=False,
        side_effects=True,
        requires_confirmation=True,
    )
    registry = build_contract_registry([skill])
    invocation = _invocation(skill, {"nebula_key": "value"})
    binding = registry.entries[0].snapshot.declared_binding
    assert binding is not None
    receipt = _Receipt(
        invocation_id=invocation.invocation_id,
        idempotency_key=invocation_signature(invocation, binding),
        effect_status=EffectStatus.VERIFIED,
    )
    calls = 0

    async def executor(_definition, _argv):
        nonlocal calls
        calls += 1
        return {"result_token": "never"}

    adapter = GenericSkillAdapter(registry, skill, executor)
    resumed = await adapter.dispatch(invocation, receipts=(receipt,))
    unknown = await adapter.dispatch(invocation, dispatch_started=True)

    assert calls == 0
    assert resumed.receipt_reused is True
    assert resumed.dispatched is False
    assert unknown.effect_status is EffectStatus.UNKNOWN
    assert unknown.status is AssetResultStatus.BLOCKED


@pytest.mark.asyncio
async def test_side_effect_requires_explicit_authorization() -> None:
    skill = _definition()
    skill.capability = CapabilityMetadata(
        declared=True,
        read_only=False,
        side_effects=True,
        requires_confirmation=True,
    )
    registry = build_contract_registry([skill])
    invocation = _invocation(skill, {"nebula_key": "value"})
    calls = 0

    async def executor(_definition, _argv):
        nonlocal calls
        calls += 1
        return {"result_token": "ok"}

    response = await GenericSkillAdapter(registry, skill, executor).dispatch(invocation)

    assert calls == 0
    assert response.effect_status is EffectStatus.CONFIRMATION_REQUIRED
    assert response.error_code == "effect.authorization_required"


@pytest.mark.asyncio
async def test_failed_skill_result_is_fail_closed_without_redispatch() -> None:
    skill = _definition()
    skill.capability = CapabilityMetadata(
        declared=True,
        read_only=False,
        side_effects=True,
        requires_confirmation=True,
    )
    registry = build_contract_registry([skill])
    invocation = _invocation(skill, {"nebula_key": "value"})
    calls = 0

    async def executor(_definition, _argv):
        nonlocal calls
        calls += 1
        return SkillResult(success=False, exit_code=1, error="failed")

    adapter = GenericSkillAdapter(registry, skill, executor)
    failed = await adapter.dispatch(invocation, authorized=True)
    blocked = await adapter.dispatch(
        invocation,
        authorized=True,
        dispatch_started=True,
    )

    assert calls == 1
    assert failed.status is AssetResultStatus.BLOCKED
    assert failed.effect_status is EffectStatus.UNKNOWN
    assert failed.dispatched is True
    assert failed.error_code == "executor.failed"
    assert blocked.status is AssetResultStatus.BLOCKED
    assert blocked.dispatched is False
    assert blocked.error_code == "effect.redispatch_blocked"
