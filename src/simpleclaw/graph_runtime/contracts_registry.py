"""Discovery 결과만으로 LangGraph V4 contract registry snapshot을 만든다.

Runtime Core는 이 모듈에서 Skill/Recipe concrete type이나 업무별 contract ID를
import하지 않는다. Discovery definition이 제공하는 공통 protocol만 읽고 owner,
schema, definition, binding identity를 한 fingerprint에 고정한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import JsonValue

from simpleclaw.capability import (
    CapabilityMetadata,
    OwnedBindingMetadata,
    OwnedContractMetadata,
)

from .contracts import (
    AssetBindingRefV1,
    AssetDefinitionSnapshotV1,
    AssetRefV1,
    ContractDescriptorV1,
    ContractRefV1,
    RetryPolicyV1,
)


class ContractRegistryError(ValueError):
    """Registry build/lookup/validation을 fail-closed로 중단하는 안정적 오류."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ContractAssetDefinition(Protocol):
    """Concrete Recipe/Skill import 없이 discovery definition을 읽는 공통 표면."""

    name: str
    capability: CapabilityMetadata
    input_contract: OwnedContractMetadata | None
    output_contract: OwnedContractMetadata | None

    @property
    def contract_asset_type(self) -> str: ...

    @property
    def contract_binding(self) -> OwnedBindingMetadata | None: ...

    @property
    def definition_fingerprint(self) -> str: ...


@dataclass(frozen=True)
class CanonicalPayload:
    """Schema 검증을 통과한 immutable canonical JSON과 continuity hash."""

    payload_json: str
    payload_hash: str

    @property
    def payload(self) -> dict[str, JsonValue]:
        """호출자 변경이 canonical 원본에 닿지 않는 payload 복사본을 반환한다."""
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # pragma: no cover - 생성 불변식
            raise TypeError("canonical payload must decode to an object")
        return value


@dataclass(frozen=True)
class RegistryAssetEntryV1:
    """한 discovery definition에서 원자적으로 만든 registry 항목."""

    snapshot: AssetDefinitionSnapshotV1
    input_descriptor: ContractDescriptorV1
    output_descriptor: ContractDescriptorV1


@dataclass(frozen=True)
class ContractRegistrySnapshotV1:
    """정렬된 registry 항목과 전체 definition fingerprint를 보존한다."""

    entries: tuple[RegistryAssetEntryV1, ...]
    fingerprint: str

    def asset(self, ref: AssetRefV1) -> RegistryAssetEntryV1 | None:
        """정확히 하나인 owner-qualified asset 항목만 반환한다."""
        matches = tuple(item for item in self.entries if item.snapshot.asset_ref == ref)
        return matches[0] if len(matches) == 1 else None

    def resolve(
        self,
        ref: ContractRefV1,
        *,
        owner: AssetRefV1,
    ) -> ContractDescriptorV1:
        """Owner와 ref 전체가 snapshot에 일치하는 contract를 조회한다."""
        if ref.owner_ref != owner:
            raise ContractRegistryError("contract.owner_mismatch")
        matches = tuple(
            descriptor
            for entry in self.entries
            for descriptor in (entry.input_descriptor, entry.output_descriptor)
            if descriptor.ref == ref
        )
        if len(matches) != 1:
            code = "contract.not_found" if not matches else "contract.duplicate"
            raise ContractRegistryError(code)
        return matches[0]

    def dispatch_candidate(
        self,
        *,
        asset_ref: AssetRefV1,
        definition_fingerprint: str,
        input_contract: ContractRefV1,
        output_contract: ContractRefV1,
        binding_ref: AssetBindingRefV1,
        registry_fingerprint: str,
    ) -> RegistryAssetEntryV1 | None:
        """모든 snapshot identity가 같은 경우에만 dispatch 후보 하나를 만든다."""
        if registry_fingerprint != self.fingerprint:
            return None
        entry = self.asset(asset_ref)
        if entry is None:
            return None
        snapshot = entry.snapshot
        if (
            definition_fingerprint != snapshot.definition_fingerprint
            or input_contract != snapshot.input_contract
            or output_contract != snapshot.output_contract
            or binding_ref != snapshot.declared_binding
        ):
            return None
        return entry

    def validate_canonical(
        self,
        descriptor: ContractDescriptorV1,
        payload: Mapping[str, JsonValue],
    ) -> CanonicalPayload:
        """Asset-owned schema를 해석 없이 적용하고 canonical payload를 만든다."""
        try:
            copied = json.loads(
                json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ContractRegistryError("payload.non_json") from exc
        _validate_schema(descriptor.json_schema, copied, path="$")
        canonical = json.dumps(
            copied,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return CanonicalPayload(
            payload_json=canonical,
            payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


def _contract_ref(metadata: OwnedContractMetadata) -> ContractRefV1:
    """Asset-owned parser 결과를 Core contract ref로 투영한다."""
    return ContractRefV1(
        contract_id=metadata.contract_id,
        version=metadata.version,
        owner_ref=AssetRefV1(type=metadata.owner_type, name=metadata.owner_name),
        schema_hash=metadata.schema_hash,
    )


def _binding_ref(metadata: OwnedBindingMetadata) -> AssetBindingRefV1:
    """Opaque binding metadata에서 identity만 Core snapshot에 투영한다."""
    return AssetBindingRefV1(
        owner_ref=AssetRefV1(type=metadata.owner_type, name=metadata.owner_name),
        binding_id=metadata.binding_id,
        binding_hash=metadata.binding_hash,
    )


def _retry_policy(definition: ContractAssetDefinition) -> RetryPolicyV1:
    """서로 다른 legacy retry 모델을 보수적인 공통 snapshot으로 축약한다."""
    raw = getattr(definition, "retry_policy", None)
    if raw is None:
        return RetryPolicyV1()
    idempotent = bool(getattr(raw, "idempotent", False))
    retries = int(getattr(raw, "max_retries", 0)) if idempotent else 0
    return RetryPolicyV1(
        max_attempts=retries + 1,
        idempotent=idempotent,
        retry_timeouts=bool(getattr(raw, "retry_on_timeout", False)),
    )


def _entry(definition: ContractAssetDefinition) -> RegistryAssetEntryV1 | None:
    """완전한 opt-in V4 definition 하나를 registry 항목으로 변환한다."""
    metadata = (
        definition.input_contract,
        definition.output_contract,
        definition.contract_binding,
    )
    if not any(item is not None for item in metadata):
        return None
    if not all(item is not None for item in metadata):
        raise ContractRegistryError("definition.contract_metadata_incomplete")
    input_metadata = definition.input_contract
    output_metadata = definition.output_contract
    binding_metadata = definition.contract_binding
    assert input_metadata is not None
    assert output_metadata is not None
    assert binding_metadata is not None
    for contract in (input_metadata, output_metadata):
        if hashlib.sha256(contract.schema_json.encode("utf-8")).hexdigest() != (
            contract.schema_hash
        ):
            raise ContractRegistryError("definition.schema_hash_mismatch")
    if hashlib.sha256(binding_metadata.binding_json.encode("utf-8")).hexdigest() != (
        binding_metadata.binding_hash
    ):
        raise ContractRegistryError("definition.binding_hash_mismatch")
    asset_ref = AssetRefV1(type=definition.contract_asset_type, name=definition.name)
    input_ref = _contract_ref(input_metadata)
    output_ref = _contract_ref(output_metadata)
    binding_ref = _binding_ref(binding_metadata)
    if any(
        owner != asset_ref
        for owner in (input_ref.owner_ref, output_ref.owner_ref, binding_ref.owner_ref)
    ):
        raise ContractRegistryError("definition.owner_mismatch")
    capability = definition.capability
    snapshot = AssetDefinitionSnapshotV1(
        asset_ref=asset_ref,
        definition_id=f"{asset_ref.type}:{asset_ref.name}",
        definition_fingerprint=definition.definition_fingerprint,
        input_contract=input_ref,
        output_contract=output_ref,
        declared=True,
        declared_binding=binding_ref,
        read_only=capability.read_only,
        side_effects=capability.side_effects,
        requires_confirmation=capability.requires_confirmation,
        retry_policy=_retry_policy(definition),
    )
    return RegistryAssetEntryV1(
        snapshot=snapshot,
        input_descriptor=ContractDescriptorV1(
            ref=input_ref,
            json_schema=input_metadata.json_schema,
            binding_ref=binding_ref,
        ),
        output_descriptor=ContractDescriptorV1(
            ref=output_ref,
            json_schema=output_metadata.json_schema,
            binding_ref=None,
        ),
    )


def build_contract_registry(
    definitions: Iterable[ContractAssetDefinition],
) -> ContractRegistrySnapshotV1:
    """현재 discovery definition만 사용해 deterministic registry를 만든다."""
    entries = tuple(
        sorted(
            (item for definition in definitions if (item := _entry(definition))),
            key=lambda item: (
                item.snapshot.asset_ref.type,
                item.snapshot.asset_ref.name.casefold(),
                item.snapshot.asset_ref.name,
            ),
        )
    )
    asset_keys = [
        (item.snapshot.asset_ref.type, item.snapshot.asset_ref.name) for item in entries
    ]
    if len(asset_keys) != len(set(asset_keys)):
        raise ContractRegistryError("definition.duplicate_asset")
    contract_keys = [
        (
            descriptor.ref.owner_ref.type,
            descriptor.ref.owner_ref.name,
            descriptor.ref.contract_id,
            descriptor.ref.version,
        )
        for entry in entries
        for descriptor in (entry.input_descriptor, entry.output_descriptor)
    ]
    if len(contract_keys) != len(set(contract_keys)):
        raise ContractRegistryError("contract.duplicate")
    payload = [
        {
            "snapshot": item.snapshot.model_dump(mode="json"),
            "input": item.input_descriptor.model_dump(mode="json"),
            "output": item.output_descriptor.model_dump(mode="json"),
        }
        for item in entries
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return ContractRegistrySnapshotV1(
        entries=entries,
        fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _validate_schema(schema: object, value: object, *, path: str) -> None:
    """Core 분기 없이 bounded JSON Schema subset을 재귀 검증한다."""
    if not isinstance(schema, dict):
        raise ContractRegistryError("schema.invalid")
    supported = {
        "$id",
        "$schema",
        "title",
        "description",
        "default",
        "examples",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "enum",
        "const",
        "allOf",
        "anyOf",
        "oneOf",
    }
    if set(schema) - supported:
        raise ContractRegistryError("schema.unsupported_keyword")
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        variants = schema[keyword]
        if not isinstance(variants, list) or not variants:
            raise ContractRegistryError("schema.invalid")
        matches = 0
        for variant in variants:
            try:
                _validate_schema(variant, value, path=path)
            except ContractRegistryError:
                continue
            matches += 1
        if keyword == "allOf" and matches != len(variants):
            raise ContractRegistryError(f"payload.schema_mismatch:{path}")
        if keyword == "anyOf" and matches == 0:
            raise ContractRegistryError(f"payload.schema_mismatch:{path}")
        if keyword == "oneOf" and matches != 1:
            raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    if "const" in schema and value != schema["const"]:
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or value not in enum):
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    expected = schema.get("type")
    if expected is not None:
        types = [expected] if isinstance(expected, str) else expected
        if not isinstance(types, list) or not all(isinstance(item, str) for item in types):
            raise ContractRegistryError("schema.invalid")
        if not any(_matches_type(item, value) for item in types):
            raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    if isinstance(value, dict):
        _validate_object(schema, value, path=path)
    elif isinstance(value, list):
        _validate_array(schema, value, path=path)
    elif isinstance(value, str):
        _validate_string(schema, value, path=path)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        _validate_number(schema, value, path=path)


def _matches_type(expected: str, value: object) -> bool:
    """JSON primitive type을 bool/int 혼동 없이 판별한다."""
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, int | float) and not isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _validate_object(schema: dict[str, Any], value: dict[str, Any], *, path: str) -> None:
    """Object property, required, additionalProperties 제약을 적용한다."""
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ContractRegistryError("schema.invalid")
    if any(not isinstance(item, str) for item in required):
        raise ContractRegistryError("schema.invalid")
    missing = set(required) - set(value)
    if missing:
        raise ContractRegistryError(f"payload.required_missing:{path}")
    additional = schema.get("additionalProperties", True)
    for key, item in value.items():
        child_path = f"{path}.{key}"
        if key in properties:
            _validate_schema(properties[key], item, path=child_path)
        elif additional is False:
            raise ContractRegistryError(f"payload.unknown_key:{child_path}")
        elif isinstance(additional, dict):
            _validate_schema(additional, item, path=child_path)


def _validate_array(schema: dict[str, Any], value: list[Any], *, path: str) -> None:
    """Array item과 bounded length 제약을 적용한다."""
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if minimum is not None and len(value) < minimum:
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    if maximum is not None and len(value) > maximum:
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    items = schema.get("items")
    if items is not None:
        for index, item in enumerate(value):
            _validate_schema(items, item, path=f"{path}[{index}]")


def _validate_string(schema: dict[str, Any], value: str, *, path: str) -> None:
    """String length와 정규식 제약을 적용한다."""
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
    if "pattern" in schema:
        try:
            matched = re.search(schema["pattern"], value)
        except (re.error, TypeError) as exc:
            raise ContractRegistryError("schema.invalid") from exc
        if matched is None:
            raise ContractRegistryError(f"payload.schema_mismatch:{path}")


def _validate_number(
    schema: dict[str, Any],
    value: int | float,
    *,
    path: str,
) -> None:
    """Numeric inclusive/exclusive boundary를 적용한다."""
    try:
        invalid = (
            ("minimum" in schema and value < schema["minimum"])
            or ("maximum" in schema and value > schema["maximum"])
            or (
                "exclusiveMinimum" in schema
                and value <= schema["exclusiveMinimum"]
            )
            or (
                "exclusiveMaximum" in schema
                and value >= schema["exclusiveMaximum"]
            )
        )
    except TypeError as exc:
        raise ContractRegistryError("schema.invalid") from exc
    if invalid:
        raise ContractRegistryError(f"payload.schema_mismatch:{path}")
