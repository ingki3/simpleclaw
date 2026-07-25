"""LLM tool call을 dispatch 직전에 검증하는 순수 로컬 gate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolRisk,
    ToolScope,
    build_native_tool_registry,
)
from simpleclaw.llm.models import ToolCall

_CRON_MUTATION_ACTIONS = frozenset({"add", "remove", "enable", "disable"})


@dataclass(frozen=True)
class ToolExecutionScope:
    """한 tool loop에서 planner/controller가 허용한 실행 범위."""

    allowed_tools: frozenset[str]
    allowed_assets: frozenset[tuple[str, str]]
    operator_tools: bool
    allow_cron_mutation: bool


class ToolCallRejected(RuntimeError):
    """dispatch 전에 반환할 수 있는 안정적인 tool gate 오류."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ToolGate:
    """호출별 allowlist와 기존 native registry scope/risk를 함께 적용한다."""

    def __init__(
        self,
        *,
        native_specs: Iterable[NativeToolSpec] | None = None,
    ) -> None:
        specs = (
            build_native_tool_registry(
                cron_available=True,
                browser_handoff_available=True,
                scopes=(
                    ToolScope.RUNTIME,
                    ToolScope.OPERATOR,
                    ToolScope.DEVELOPMENT,
                ),
                operator_gate=True,
            )
            if native_specs is None
            else tuple(native_specs)
        )
        self._specs: dict[str, NativeToolSpec] = {}
        for spec in specs:
            self._specs[spec.definition.name] = spec
            for alias in spec.aliases:
                self._specs[alias] = spec

    def authorize(
        self,
        call: ToolCall,
        scope: ToolExecutionScope,
    ) -> None:
        """허용된 호출이면 None을 반환하고 아니면 stable code로 거부한다."""
        if call.name not in scope.allowed_tools:
            raise ToolCallRejected("tool_not_allowed")

        if call.name == "execute_skill":
            skill = str(call.arguments.get("skill_name") or "").strip()
            if ("skill", skill) not in scope.allowed_assets:
                raise ToolCallRejected("skill_not_allowed")
            return

        spec = self._specs.get(call.name)
        if spec is None:
            raise ToolCallRejected("tool_not_registered")
        if (
            spec.scope is not ToolScope.RUNTIME
            or spec.operator_gate_required
        ) and not scope.operator_tools:
            raise ToolCallRejected("operator_tool_not_allowed")
        if spec.risk is ToolRisk.HIGH and not scope.operator_tools:
            raise ToolCallRejected("high_risk_tool_not_allowed")
        if (
            spec.definition.name == "cron"
            and not scope.allow_cron_mutation
            and str(call.arguments.get("cron_action") or "").strip()
            in _CRON_MUTATION_ACTIONS
        ):
            raise ToolCallRejected("cron_mutation_not_allowed")
