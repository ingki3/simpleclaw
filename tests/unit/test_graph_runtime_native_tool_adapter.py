from __future__ import annotations

import pytest

from simpleclaw.agent.tool_schemas import ToolScope, build_native_tool_registry
from simpleclaw.graph_runtime.adapters.native_tool import GenericNativeToolAdapter
from simpleclaw.graph_runtime.contracts import AssetInvocationV1
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus


def _web_search_spec():
    return next(
        spec
        for spec in build_native_tool_registry(scopes=(ToolScope.RUNTIME,))
        if spec.name == "web_search"
    )


@pytest.mark.asyncio
async def test_native_function_arguments_and_text_output_are_normalized() -> None:
    spec = _web_search_spec()
    registry = build_contract_registry((spec,))
    entry = registry.entries[0]
    payload = {"query": "LangGraph V4", "limit": 3}
    canonical = registry.validate_canonical(entry.input_descriptor, payload)
    invocation = AssetInvocationV1(
        invocation_id="native-1",
        asset_ref=entry.snapshot.asset_ref,
        definition_fingerprint=spec.definition_fingerprint,
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )
    calls = []

    async def executor(exact_spec, arguments):
        calls.append((exact_spec, arguments))
        return "WEB_SEARCH_RESULTS"

    response = await GenericNativeToolAdapter(
        registry, spec, executor
    ).dispatch(invocation)

    assert calls == [(spec, payload)]
    assert response.status is AssetResultStatus.RESOLVED
    assert response.effect_status is EffectStatus.NONE
    assert response.result is not None
    assert response.result.payload == {"content": "WEB_SEARCH_RESULTS"}


@pytest.mark.asyncio
async def test_native_definition_drift_dispatches_zero() -> None:
    spec = _web_search_spec()
    registry = build_contract_registry((spec,))
    entry = registry.entries[0]
    canonical = registry.validate_canonical(
        entry.input_descriptor, {"query": "LangGraph V4"}
    )
    invocation = AssetInvocationV1(
        invocation_id="native-2",
        asset_ref=entry.snapshot.asset_ref,
        definition_fingerprint="stale",
        input_contract=entry.input_descriptor.ref,
        payload=canonical.payload,
        payload_hash=canonical.payload_hash,
        output_contract=entry.output_descriptor.ref,
    )
    calls = 0

    async def executor(_spec, _arguments):
        nonlocal calls
        calls += 1
        return "never"

    response = await GenericNativeToolAdapter(
        registry, spec, executor
    ).dispatch(invocation)

    assert calls == 0
    assert response.error_code == "definition.drift"
    assert response.dispatched is False
