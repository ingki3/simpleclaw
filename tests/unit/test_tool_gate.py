"""BIZ-492 — tool call별 로컬 allowlist/scope gate 계약."""

from __future__ import annotations

import pytest

from simpleclaw.agent.tool_gate import (
    ToolCallRejected,
    ToolExecutionScope,
    ToolGate,
    TrustedAssetSafety,
)
from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolRisk,
    ToolScope,
)
from simpleclaw.llm.models import ToolCall, ToolDefinition


def _spec(
    name: str,
    *,
    scope: ToolScope = ToolScope.RUNTIME,
    risk: ToolRisk = ToolRisk.LOW,
    operator_gate_required: bool = False,
) -> NativeToolSpec:
    return NativeToolSpec(
        definition=ToolDefinition(name=name, description="", parameters={}),
        scope=scope,
        risk=risk,
        operator_gate_required=operator_gate_required,
    )


def _scope(
    *tools: str,
    assets: frozenset[tuple[str, str]] = frozenset(),
    operator_tools: bool = False,
    allow_cron_mutation: bool = False,
) -> ToolExecutionScope:
    return ToolExecutionScope(
        allowed_tools=frozenset(tools),
        allowed_assets=assets,
        operator_tools=operator_tools,
        allow_cron_mutation=allow_cron_mutation,
    )


def test_tool_outside_allowlist_is_rejected() -> None:
    gate = ToolGate(native_specs=[_spec("web_search")])

    with pytest.raises(ToolCallRejected, match="tool_not_allowed") as exc:
        gate.authorize(
            ToolCall(id="1", name="web_search", arguments={}),
            _scope("file_read"),
        )

    assert exc.value.code == "tool_not_allowed"


def test_execute_skill_requires_allowed_skill_identity() -> None:
    gate = ToolGate(native_specs=[])
    call = ToolCall(
        id="1",
        name="execute_skill",
        arguments={"skill_name": "weather"},
    )

    with pytest.raises(ToolCallRejected, match="skill_not_allowed") as exc:
        gate.authorize(
            call,
            _scope(
                "execute_skill",
                assets=frozenset({("skill", "stocks")}),
            ),
        )

    assert exc.value.code == "skill_not_allowed"


def test_allowlisted_skill_is_authorized() -> None:
    ToolGate(native_specs=[]).authorize(
        ToolCall(
            id="1",
            name="execute_skill",
            arguments={"skill_name": "weather"},
        ),
        _scope(
            "execute_skill",
            assets=frozenset({("skill", "weather")}),
        ),
    )


@pytest.mark.parametrize(
    ("safety", "expected_code"),
    [
        ((), "skill_safety_metadata_missing"),
        (
            (TrustedAssetSafety("skill", "weather", True, False, False, False),),
            "skill_not_safe_for_exact_read_only",
        ),
        (
            (TrustedAssetSafety("skill", "weather", True, True, True, False),),
            "skill_not_safe_for_exact_read_only",
        ),
        (
            (TrustedAssetSafety("skill", "weather", True, True, False, True),),
            "skill_not_safe_for_exact_read_only",
        ),
        (
            (TrustedAssetSafety("skill", "stocks", True, True, False, False),),
            "skill_safety_metadata_missing",
        ),
    ],
    ids=("missing", "write", "side-effect", "confirmation", "asset-mismatch"),
)
def test_exact_skill_scope_requires_matching_trusted_read_only_safety(
    safety: tuple[TrustedAssetSafety, ...],
    expected_code: str,
) -> None:
    scope = ToolExecutionScope(
        allowed_tools=frozenset({"execute_skill"}),
        allowed_assets=frozenset({("skill", "weather")}),
        operator_tools=False,
        allow_cron_mutation=False,
        max_tool_calls=1,
        trusted_asset_safety=safety,
    )

    with pytest.raises(ToolCallRejected) as exc:
        ToolGate(native_specs=[]).authorize(
            ToolCall(
                id="1",
                name="execute_skill",
                arguments={"skill_name": "weather"},
            ),
            scope,
        )

    assert exc.value.code == expected_code


def test_exact_skill_scope_authorizes_matching_trusted_read_only_safety() -> None:
    ToolGate(native_specs=[]).authorize(
        ToolCall(
            id="1",
            name="execute_skill",
            arguments={"skill_name": "weather"},
        ),
        ToolExecutionScope(
            allowed_tools=frozenset({"execute_skill"}),
            allowed_assets=frozenset({("skill", "weather")}),
            operator_tools=False,
            allow_cron_mutation=False,
            max_tool_calls=1,
            trusted_asset_safety=(
                TrustedAssetSafety("skill", "weather", True, True, False, False),
            ),
        ),
    )


def test_operator_tool_requires_operator_scope() -> None:
    gate = ToolGate(
        native_specs=[
            _spec(
                "runtime_status",
                scope=ToolScope.OPERATOR,
                operator_gate_required=True,
            )
        ]
    )
    call = ToolCall(id="1", name="runtime_status", arguments={})

    with pytest.raises(ToolCallRejected) as exc:
        gate.authorize(call, _scope("runtime_status"))
    assert exc.value.code == "operator_tool_not_allowed"

    gate.authorize(call, _scope("runtime_status", operator_tools=True))


def test_unregistered_native_tool_is_rejected() -> None:
    with pytest.raises(ToolCallRejected) as exc:
        ToolGate(native_specs=[]).authorize(
            ToolCall(id="1", name="invented", arguments={}),
            _scope("invented"),
        )

    assert exc.value.code == "tool_not_registered"


@pytest.mark.parametrize("action", ["add", "remove", "enable", "disable"])
def test_cron_mutation_requires_explicit_scope(action: str) -> None:
    gate = ToolGate(native_specs=[_spec("cron", risk=ToolRisk.MEDIUM)])
    call = ToolCall(
        id="1",
        name="cron",
        arguments={"cron_action": action},
    )

    with pytest.raises(ToolCallRejected) as exc:
        gate.authorize(call, _scope("cron", allow_cron_mutation=False))
    assert exc.value.code == "cron_mutation_not_allowed"

    gate.authorize(call, _scope("cron", allow_cron_mutation=True))


def test_cron_read_is_allowed_without_mutation_scope() -> None:
    ToolGate(native_specs=[_spec("cron", risk=ToolRisk.MEDIUM)]).authorize(
        ToolCall(id="1", name="cron", arguments={"cron_action": "list"}),
        _scope("cron", allow_cron_mutation=False),
    )
