"""LLM tool call을 dispatch 직전에 검증하는 순수 로컬 gate."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolRisk,
    ToolScope,
    build_native_tool_registry,
)
from simpleclaw.llm.models import ToolCall
from simpleclaw.skills.models import SkillDefinition

_CRON_MUTATION_ACTIONS = frozenset({"add", "remove", "enable", "disable"})


def skill_definition_fingerprint(skill: SkillDefinition) -> str:
    """실행·안전성에 영향을 주는 SkillDefinition 전체를 canonical hash로 고정한다."""
    payload = {
        "name": skill.name,
        "description": skill.description,
        "script_path": skill.script_path,
        "trigger": skill.trigger,
        "scope": skill.scope.value,
        "skill_dir": skill.skill_dir,
        "commands": list(skill.commands),
        "retry_policy": (
            asdict(skill.retry_policy) if skill.retry_policy is not None else None
        ),
        "capability": asdict(skill.capability),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TrustedAssetSafety:
    """Discovered asset definition에서 파생된 실행 안전성 snapshot."""

    asset_type: str
    asset_name: str
    declared: bool
    read_only: bool
    side_effects: bool
    requires_confirmation: bool
    definition_identity: int | None = None
    definition_fingerprint: str = ""

    @classmethod
    def from_skill(cls, skill: SkillDefinition) -> TrustedAssetSafety:
        """실제 resolved definition에서 immutable dispatch snapshot을 만든다."""
        return cls(
            asset_type="skill",
            asset_name=skill.name,
            declared=skill.capability.declared,
            read_only=skill.capability.read_only,
            side_effects=skill.capability.side_effects,
            requires_confirmation=skill.capability.requires_confirmation,
            definition_identity=id(skill),
            definition_fingerprint=skill_definition_fingerprint(skill),
        )

    @property
    def safe_for_exact_read_only(self) -> bool:
        return (
            self.declared
            and self.read_only
            and not self.side_effects
            and not self.requires_confirmation
            and self.definition_identity is not None
            and bool(self.definition_fingerprint)
        )

    def matches_definition(self, skill: SkillDefinition) -> bool:
        """동일 객체이며 scope 생성 뒤 내용도 바뀌지 않은 경우만 허용한다."""
        return (
            self.definition_identity is not None
            and bool(self.definition_fingerprint)
            and self.definition_identity == id(skill)
            and self.definition_fingerprint == skill_definition_fingerprint(skill)
        )


@dataclass(frozen=True)
class ToolExecutionScope:
    """한 tool loop에서 planner/controller가 허용한 실행 범위."""

    allowed_tools: frozenset[str]
    allowed_assets: frozenset[tuple[str, str]]
    operator_tools: bool
    allow_cron_mutation: bool
    # None은 기존 planned loop와 동일한 무제한(상위 iteration budget 적용).
    # exact nested recipe는 1로 고정해 delegate 중복 실행을 dispatch 전에 막는다.
    max_tool_calls: int | None = None
    # Planner/model payload가 아니라 loaded asset definition에서 만든 값만 넣는다.
    trusted_asset_safety: tuple[TrustedAssetSafety, ...] = ()

    def safety_for(self, asset_type: str, asset_name: str) -> TrustedAssetSafety | None:
        matches = tuple(
            item
            for item in self.trusted_asset_safety
            if item.asset_type == asset_type and item.asset_name == asset_name
        )
        return matches[0] if len(matches) == 1 else None


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
        *,
        resolved_skill: SkillDefinition | None = None,
    ) -> None:
        """허용된 호출이면 None을 반환하고 아니면 stable code로 거부한다."""
        if call.name not in scope.allowed_tools:
            raise ToolCallRejected("tool_not_allowed")

        if call.name == "execute_skill":
            skill = str(call.arguments.get("skill_name") or "").strip()
            if ("skill", skill) not in scope.allowed_assets:
                raise ToolCallRejected("skill_not_allowed")
            if scope.max_tool_calls == 1:
                safety = scope.safety_for("skill", skill)
                if safety is None:
                    raise ToolCallRejected("skill_safety_metadata_missing")
                if not (
                    safety.declared
                    and safety.read_only
                    and not safety.side_effects
                    and not safety.requires_confirmation
                ):
                    raise ToolCallRejected("skill_not_safe_for_exact_read_only")
                if (
                    safety.definition_identity is None
                    or not safety.definition_fingerprint
                ):
                    raise ToolCallRejected("skill_definition_fingerprint_missing")
                if resolved_skill is None:
                    raise ToolCallRejected("skill_definition_missing")
                if safety.definition_identity != id(resolved_skill):
                    raise ToolCallRejected("skill_definition_identity_mismatch")
                if not safety.matches_definition(resolved_skill):
                    raise ToolCallRejected("skill_definition_fingerprint_mismatch")
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
