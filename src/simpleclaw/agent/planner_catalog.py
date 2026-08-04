"""Unified TurnPlanner용 compact capability catalog.

Planner에는 실행 스키마, 명령, 파일 경로 대신 자산 선택에 필요한 짧은 설명과
capability/safety metadata만 제공한다. 같은 불변 snapshot의 fingerprint를
PlanGate가 함께 사용하면 planner 호출과 runtime registry 사이의 drift를 감지할
수 있다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from simpleclaw.agent.tool_schemas import (
    NativeToolSpec,
    ToolRisk,
    ToolScope,
    build_native_tool_registry,
)
from simpleclaw.capability import CapabilityMetadata
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.models import SkillDefinition

DESCRIPTION_MAX_CHARS = 96
_EXECUTE_SKILL_TOOL_NAME = "execute_skill"
_ASSET_TYPES = frozenset({"native_tool", "skill", "recipe"})
_ALL_NATIVE_SCOPES = (
    ToolScope.RUNTIME,
    ToolScope.OPERATOR,
    ToolScope.DEVELOPMENT,
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w:/])/(?!/)[^\s/]+(?:/[^\s/]+)*"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/]|\\\\[^\\\s]+[\\/])[^\s]+"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:"
    r"api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd|"
    r"client[_-]?secret|authorization"
    r")\b\s*[:=]\s*['\"]?[^\s,;'\"`]+"
)
_CREDENTIAL_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:sk|gh[pousr])[-_][A-Za-z0-9._-]{8,}|"
    r"\bAIza[0-9A-Za-z_-]{10,}|"
    r"\b[A-Za-z0-9_]{16,}:[A-Za-z0-9_-]{20,}\b"
    r")"
)
_SENSITIVE_TEXT_PATTERNS = (
    ("absolute_path", _POSIX_ABSOLUTE_PATH_RE),
    ("absolute_path", _WINDOWS_ABSOLUTE_PATH_RE),
    ("credential", _CREDENTIAL_ASSIGNMENT_RE),
    ("credential", _CREDENTIAL_TOKEN_RE),
)


class PlannerCatalogSensitiveTextError(ValueError):
    """민감 원문 없이 catalog 직렬화를 중단하는 명시적 보안 오류."""

    def __init__(
        self,
        *,
        reason: str,
        asset_type: str,
        asset_name: str | None,
        field: str,
    ) -> None:
        self.reason = reason
        self.code = f"planner_catalog_sensitive_text.{reason}"
        self.asset_type = asset_type
        self.asset_name = asset_name
        self.field = field
        identity = (
            asset_type
            if asset_name is None
            else f"{asset_type}/{asset_name}"
        )
        super().__init__(
            f"{self.code}: asset={identity} field={field}"
        )


def _validate_catalog_text(
    value: object,
    *,
    asset_type: str,
    asset_name: str | None,
    field: str,
) -> str:
    """Planner payload/fingerprint에 들어갈 문자열을 fail-closed 검증한다."""
    text = str(value or "")
    for reason, pattern in _SENSITIVE_TEXT_PATTERNS:
        if pattern.search(text):
            raise PlannerCatalogSensitiveTextError(
                reason=reason,
                asset_type=asset_type,
                asset_name=asset_name,
                field=field,
            )
    return text


@dataclass(frozen=True)
class PlannerAsset:
    """Planner가 자산 선택에 사용하는 경로·secret-free 공통 shape."""

    asset_type: str
    name: str
    description: str
    domains: tuple[str, ...]
    intents: tuple[str, ...]
    read_only: bool
    side_effects: bool
    freshness_sensitive: bool
    direct_answer: bool
    requires_confirmation: bool
    output_contract: str | None
    declared: bool
    runtime_visible: bool
    coverage: str = "partial_coverage"
    input_contract: str | None = None
    fallback_modes: tuple[str, ...] = ()
    retry_statuses: tuple[str, ...] = ()
    contract_owner: str | None = None
    input_contract_ref: str | None = None
    output_contract_ref: str | None = None
    input_schema_hash: str | None = None
    output_schema_hash: str | None = None
    binding_identity: str | None = None
    definition_fingerprint: str | None = None

    def __post_init__(self) -> None:
        """알 수 없거나 민감한 자산 문자열은 snapshot에 들어오지 못하게 한다."""
        if self.asset_type not in _ASSET_TYPES:
            raise ValueError(f"unsupported planner asset_type: {self.asset_type}")
        if not self.name.strip():
            raise ValueError("planner asset name must not be empty")
        safe_name = self.name.strip()
        _validate_catalog_text(
            self.name,
            asset_type=self.asset_type,
            asset_name=None,
            field="name",
        )
        _validate_catalog_text(
            self.description,
            asset_type=self.asset_type,
            asset_name=safe_name,
            field="description",
        )
        for domain in self.domains:
            _validate_catalog_text(
                domain,
                asset_type=self.asset_type,
                asset_name=safe_name,
                field="domain",
            )
        for intent in self.intents:
            _validate_catalog_text(
                intent,
                asset_type=self.asset_type,
                asset_name=safe_name,
                field="intent",
            )
        if self.output_contract is not None:
            _validate_catalog_text(
                self.output_contract,
                asset_type=self.asset_type,
                asset_name=safe_name,
                field="output_contract",
            )
        if self.input_contract is not None:
            _validate_catalog_text(
                self.input_contract,
                asset_type=self.asset_type,
                asset_name=safe_name,
                field="input_contract",
            )
        for field_name in (
            "contract_owner",
            "input_contract_ref",
            "output_contract_ref",
            "input_schema_hash",
            "output_schema_hash",
            "binding_identity",
            "definition_fingerprint",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_catalog_text(
                    value,
                    asset_type=self.asset_type,
                    asset_name=safe_name,
                    field=field_name,
                )


@dataclass(frozen=True)
class PlannerCatalog:
    """정렬된 자산 snapshot과 canonical fingerprint."""

    assets: tuple[PlannerAsset, ...]
    fingerprint: str

    def to_prompt_json(self, *, runtime_only: bool = True) -> str:
        """Planner 입력용 compact JSON array를 반환한다.

        기본 runtime planner에서는 operator/development native tool을 제외한다.
        진단·PlanGate가 전체 snapshot을 확인해야 할 때만 ``runtime_only=False``를
        명시한다. 경로, command, parameter schema는 어느 경우에도 직렬화하지 않는다.
        """
        assets = (
            asset for asset in self.assets
            if asset.runtime_visible or not runtime_only
        )
        return _canonical_json([_prompt_payload(asset) for asset in assets])


def _canonical_json(value: object) -> str:
    """fingerprint와 prompt가 공유하는 deterministic compact JSON."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _compact_description(
    value: object,
    *,
    asset_type: str,
    asset_name: str,
) -> str:
    """민감 원문을 거부한 뒤 설명을 정규화하고 고정 길이로 clamp한다."""
    validated = _validate_catalog_text(
        value,
        asset_type=asset_type,
        asset_name=asset_name,
        field="description",
    )
    compact = " ".join(validated.split())
    if len(compact) <= DESCRIPTION_MAX_CHARS:
        return compact
    return compact[: DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"


def _normalized_tuple(values: Iterable[object]) -> tuple[str, ...]:
    """capability 힌트를 순서·중복에 무관한 stable tuple로 만든다."""
    return tuple(sorted({
        str(value).strip().lower()
        for value in values
        if str(value).strip()
    }))


def _asset_from_capability(
    *,
    asset_type: str,
    name: str,
    description: str,
    capability: CapabilityMetadata,
    runtime_visible: bool,
    contract_owner: str | None = None,
    input_contract_ref: str | None = None,
    output_contract_ref: str | None = None,
    input_schema_hash: str | None = None,
    output_schema_hash: str | None = None,
    binding_identity: str | None = None,
    definition_fingerprint: str | None = None,
) -> PlannerAsset:
    """Skill/recipe 공용 CapabilityMetadata를 PlannerAsset으로 변환한다."""
    clean_name = name.strip()
    _validate_catalog_text(
        clean_name,
        asset_type=asset_type,
        asset_name=None,
        field="name",
    )
    return PlannerAsset(
        asset_type=asset_type,
        name=clean_name,
        description=_compact_description(
            description,
            asset_type=asset_type,
            asset_name=clean_name,
        ),
        domains=_normalized_tuple(capability.domains),
        intents=_normalized_tuple(capability.intents),
        read_only=capability.read_only,
        side_effects=capability.side_effects,
        freshness_sensitive=capability.freshness_sensitive,
        direct_answer=capability.direct_answer,
        requires_confirmation=capability.requires_confirmation,
        output_contract=capability.output_contract,
        coverage=capability.coverage,
        input_contract=capability.input_contract,
        fallback_modes=capability.fallback_modes,
        retry_statuses=capability.retry_statuses,
        declared=capability.declared,
        runtime_visible=runtime_visible,
        contract_owner=contract_owner,
        input_contract_ref=input_contract_ref,
        output_contract_ref=output_contract_ref,
        input_schema_hash=input_schema_hash,
        output_schema_hash=output_schema_hash,
        binding_identity=binding_identity,
        definition_fingerprint=definition_fingerprint,
    )


def _asset_from_native_spec(spec: NativeToolSpec) -> PlannerAsset:
    """기존 native scope/risk metadata를 보수적인 capability로 투영한다."""
    read_only = spec.risk is ToolRisk.LOW
    side_effects = not read_only
    clean_name = spec.definition.name.strip()
    _validate_catalog_text(
        clean_name,
        asset_type="native_tool",
        asset_name=None,
        field="name",
    )
    return PlannerAsset(
        asset_type="native_tool",
        name=clean_name,
        description=_compact_description(
            spec.definition.description,
            asset_type="native_tool",
            asset_name=clean_name,
        ),
        domains=(),
        intents=(),
        read_only=read_only,
        side_effects=side_effects,
        freshness_sensitive=False,
        direct_answer=False,
        requires_confirmation=(
            spec.operator_gate_required or spec.risk is ToolRisk.HIGH
        ),
        output_contract=None,
        coverage="partial_coverage",
        input_contract=None,
        declared=True,
        runtime_visible=spec.scope is ToolScope.RUNTIME,
    )


def _execute_skill_adapter_asset() -> PlannerAsset:
    """동적 skill registry와 planner tool 경계를 잇는 합성 실행 어댑터."""
    return PlannerAsset(
        asset_type="native_tool",
        name=_EXECUTE_SKILL_TOOL_NAME,
        description=(
            "Execute one exact allowed skill; the selected skill metadata "
            "determines safety."
        ),
        domains=(),
        intents=(),
        read_only=True,
        side_effects=False,
        freshness_sensitive=False,
        direct_answer=False,
        requires_confirmation=False,
        output_contract=None,
        coverage="partial_coverage",
        input_contract=None,
        declared=True,
        runtime_visible=True,
    )


def _snapshot_payload(asset: PlannerAsset) -> dict[str, Any]:
    """fingerprint용 전체 compact shape."""
    return {
        "type": asset.asset_type,
        "name": asset.name,
        "description": asset.description,
        "domains": list(asset.domains),
        "intents": list(asset.intents),
        "read_only": asset.read_only,
        "side_effects": asset.side_effects,
        "freshness_sensitive": asset.freshness_sensitive,
        "direct_answer": asset.direct_answer,
        "requires_confirmation": asset.requires_confirmation,
        "output_contract": asset.output_contract,
        "coverage": asset.coverage,
        "input_contract": asset.input_contract,
        "fallback_modes": list(asset.fallback_modes),
        "retry_statuses": list(asset.retry_statuses),
        "declared": asset.declared,
        "runtime_visible": asset.runtime_visible,
        "contract_owner": asset.contract_owner,
        "input_contract_ref": asset.input_contract_ref,
        "output_contract_ref": asset.output_contract_ref,
        "input_schema_hash": asset.input_schema_hash,
        "output_schema_hash": asset.output_schema_hash,
        "binding_identity": asset.binding_identity,
        "definition_fingerprint": asset.definition_fingerprint,
    }


def _prompt_payload(asset: PlannerAsset) -> dict[str, Any]:
    """Planner 입력용 shape에서 snapshot/default 전용 필드를 제외한다."""
    payload: dict[str, Any] = {
        "type": asset.asset_type,
        "name": asset.name,
        "description": asset.description,
        "read_only": asset.read_only,
        "side_effects": asset.side_effects,
        "requires_confirmation": asset.requires_confirmation,
        "declared": asset.declared,
    }
    if asset.domains:
        payload["domains"] = list(asset.domains)
    if asset.intents:
        payload["intents"] = list(asset.intents)
    if asset.freshness_sensitive:
        payload["freshness_sensitive"] = True
    if asset.direct_answer:
        payload["direct_answer"] = True
    if asset.coverage != "partial_coverage":
        payload["coverage"] = asset.coverage
    if asset.input_contract:
        payload["input_contract"] = asset.input_contract
    if asset.output_contract:
        payload["output_contract"] = asset.output_contract
    if asset.fallback_modes:
        payload["fallback_modes"] = list(asset.fallback_modes)
    if asset.retry_statuses:
        payload["retry_statuses"] = list(asset.retry_statuses)
    if asset.contract_owner is not None:
        payload["contract_owner"] = asset.contract_owner
        payload["input_contract_ref"] = asset.input_contract_ref
        payload["output_contract_ref"] = asset.output_contract_ref
        payload["input_schema_hash"] = asset.input_schema_hash
        payload["output_schema_hash"] = asset.output_schema_hash
        payload["binding_identity"] = asset.binding_identity
        payload["definition_fingerprint"] = asset.definition_fingerprint
    return payload


def _contract_catalog_fields(definition: object) -> dict[str, str | None]:
    """V4 metadata가 완전한 definition에서 provider-safe identity만 추출한다."""
    input_contract = getattr(definition, "input_contract", None)
    output_contract = getattr(definition, "output_contract", None)
    binding = getattr(definition, "contract_binding", None)
    if input_contract is None or output_contract is None or binding is None:
        return {}
    owner = f"{input_contract.owner_type}:{input_contract.owner_name}"
    return {
        "contract_owner": owner,
        "input_contract_ref": (
            f"{input_contract.contract_id}@{input_contract.version}"
        ),
        "output_contract_ref": (
            f"{output_contract.contract_id}@{output_contract.version}"
        ),
        "input_schema_hash": input_contract.schema_hash,
        "output_schema_hash": output_contract.schema_hash,
        "binding_identity": f"{binding.binding_id}:{binding.binding_hash}",
        "definition_fingerprint": definition.definition_fingerprint,
    }


def _default_native_specs(
    *,
    cron_available: bool,
    browser_handoff_available: bool,
) -> tuple[NativeToolSpec, ...]:
    """runtime/internal native asset을 한 snapshot에서 가져온다."""
    return build_native_tool_registry(
        cron_available=cron_available,
        browser_handoff_available=browser_handoff_available,
        scopes=_ALL_NATIVE_SCOPES,
        operator_gate=True,
    )


def build_planner_catalog(
    *,
    skills: Iterable[SkillDefinition] = (),
    recipes: Iterable[RecipeDefinition] = (),
    native_specs: Iterable[NativeToolSpec] | None = None,
    cron_available: bool = False,
    browser_handoff_available: bool = False,
) -> PlannerCatalog:
    """현재 runtime registry 입력에서 immutable Planner catalog를 만든다."""
    skill_definitions = tuple(skills)
    specs = (
        _default_native_specs(
            cron_available=cron_available,
            browser_handoff_available=browser_handoff_available,
        )
        if native_specs is None
        else tuple(native_specs)
    )
    assets = [
        *(_asset_from_native_spec(spec) for spec in specs),
        *(
            (_execute_skill_adapter_asset(),)
            if skill_definitions
            else ()
        ),
        *(
            _asset_from_capability(
                asset_type="skill",
                name=skill.name,
                description=skill.description,
                capability=skill.capability,
                runtime_visible=True,
                **_contract_catalog_fields(skill),
            )
            for skill in skill_definitions
        ),
        *(
            _asset_from_capability(
                asset_type="recipe",
                name=recipe.name,
                description=recipe.description,
                capability=recipe.capability,
                runtime_visible=True,
                **_contract_catalog_fields(recipe),
            )
            for recipe in recipes
        ),
    ]
    ordered = tuple(
        sorted(
            assets,
            key=lambda asset: (
                asset.asset_type,
                asset.name.casefold(),
                asset.name,
            ),
        )
    )
    seen: set[tuple[str, str]] = set()
    for asset in ordered:
        identity = (asset.asset_type, asset.name)
        if identity in seen:
            raise ValueError(
                f"duplicate planner asset: {asset.asset_type}/{asset.name}"
            )
        seen.add(identity)

    snapshot = _canonical_json([_snapshot_payload(asset) for asset in ordered])
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    return PlannerCatalog(assets=ordered, fingerprint=fingerprint)


def catalog_prompt_metrics(
    catalog: PlannerCatalog,
    *,
    runtime_only: bool = True,
) -> dict[str, int]:
    """Evaluator report에 합칠 수 있는 payload 크기 지표를 반환한다.

    tokenizer/provider에 종속되지 않는 오프라인 비교를 위해 UTF-8 JSON 문자 수와
    보수적인 4 chars/token 추정치를 함께 제공한다.
    """
    payload = catalog.to_prompt_json(runtime_only=runtime_only)
    asset_count = sum(
        asset.runtime_visible or not runtime_only for asset in catalog.assets
    )
    return {
        "asset_count": asset_count,
        "character_count": len(payload),
        "estimated_tokens": math.ceil(len(payload) / 4),
    }
