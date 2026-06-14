"""운영자 native tool registry의 metadata와 노출 gate 회귀 테스트.

BIZ-370은 운영 도구를 실제로 추가하기 전에 registry 단에서 scope/risk/operator
gate를 분리해, 일반 사용자 runtime 도구와 운영자/개발 도구가 섞여 노출되는
사고를 막는 기반을 만든다.
"""

from __future__ import annotations

import pytest

from simpleclaw.agent.builtin_tools import BUILTIN_TOOL_NAMES
from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolRisk,
    ToolScope,
    build_native_tool_registry,
    native_tool_names,
    validate_dispatch_tool_names,
)
from simpleclaw.llm.models import ToolDefinition


def _dummy_tool(name: str) -> ToolDefinition:
    """scope filtering 검증에 쓰는 최소 ToolDefinition을 만든다."""
    return ToolDefinition(
        name=name,
        description=f"dummy {name}",
        parameters={"type": "object", "properties": {}, "required": []},
    )


def test_every_registry_entry_has_scope_risk_and_gate_metadata():
    """후속 이슈가 추가할 운영 도구도 동일 metadata 계약을 강제받아야 한다."""
    registry = build_native_tool_registry(cron_available=True)

    assert registry
    for spec in registry:
        assert isinstance(spec, NativeToolSpec)
        assert spec.definition.name
        assert isinstance(spec.scope, ToolScope)
        assert isinstance(spec.risk, ToolRisk)
        if spec.scope is ToolScope.RUNTIME:
            assert not spec.operator_gate_required
        else:
            assert spec.operator_gate_required


def test_default_native_tool_names_do_not_include_operator_or_development_tools():
    """기본 이름 목록은 사용자 runtime 도구만 포함한다."""
    extra_specs = (
        NativeToolSpec(
            _dummy_tool("runtime_status"),
            scope=ToolScope.OPERATOR,
            risk=ToolRisk.HIGH,
            operator_gate_required=True,
        ),
        NativeToolSpec(
            _dummy_tool("log_debug"),
            scope=ToolScope.DEVELOPMENT,
            risk=ToolRisk.MEDIUM,
            operator_gate_required=True,
        ),
    )
    runtime_names = native_tool_names(
        cron_available=True,
        extra_specs=extra_specs,
    )
    protected_names = {
        spec.definition.name
        for spec in build_native_tool_registry(
            cron_available=True,
            scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
            operator_gate=True,
            extra_specs=extra_specs,
        )
        if spec.scope in {ToolScope.OPERATOR, ToolScope.DEVELOPMENT}
    }

    assert protected_names == {
        "runtime_status",
        "config_inspect",
        "log_debug",
        "asset_inventory",
    }
    assert runtime_names.isdisjoint(protected_names)


def test_cron_is_conditionally_exposed_through_registry():
    """cron_available=False일 때 registry 단계에서 cron이 빠져야 한다."""
    assert "cron" not in native_tool_names(cron_available=False)
    assert "cron" in native_tool_names(cron_available=True)


def test_builtin_tool_names_follow_registry_underscore_names():
    """builtin_tools의 이름 목록은 registry의 function-call 이름과 동일해야 한다."""
    assert BUILTIN_TOOL_NAMES == native_tool_names(cron_available=True)
    assert all("-" not in name for name in BUILTIN_TOOL_NAMES)


def test_dispatch_name_validation_accepts_registry_names():
    """dispatch mapping이 registry와 어긋나면 부팅 시점에 잡을 수 있어야 한다."""
    validate_dispatch_tool_names(native_tool_names(cron_available=True))


def test_dispatch_name_validation_rejects_missing_or_unknown_names():
    """dispatch mapping 검증은 누락/추가 이름을 모두 오류로 보고한다."""
    names = set(native_tool_names(cron_available=True))
    names.remove("web_fetch")
    names.add("unknown_admin_tool")

    with pytest.raises(
        ValueError,
        match="missing=.*web_fetch.*unknown=.*unknown_admin_tool",
    ):
        validate_dispatch_tool_names(names)