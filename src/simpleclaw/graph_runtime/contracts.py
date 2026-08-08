"""LangGraph V4 런타임의 엄격한 도메인 중립 데이터 계약을 정의한다."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)

from .status import AssetResultStatus, DeliveryStatus, EffectStatus, TerminalOutcome

NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]

COMPOSITION_FIELDS_EXTENSION = "x-simpleclaw-composition-fields"
STRUCTURAL_EVIDENCE_RELATIONS_EXTENSION = (
    "x-simpleclaw-structural-evidence-relations"
)
MAX_COMPOSITION_FIELD_PATHS = 64
MAX_COMPOSITION_FIELD_DEPTH = 10
MAX_COMPOSITION_RELATIONS = 8
_COMPOSITION_PATH_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*(?:\[\*\])?"
    r"(?:\.[A-Za-z_][A-Za-z0-9_-]*(?:\[\*\])?)*$"
)
_FORBIDDEN_COMPOSITION_SEGMENT_MARKERS = frozenset(
    {
        "answer",
        "apikey",
        "content",
        "credential",
        "credentials",
        "diagnostic",
        "email",
        "error",
        "internal",
        "internalprompt",
        "password",
        "private",
        "prompt",
        "provider",
        "raw",
        "secret",
        "summary",
        "text",
        "token",
    }
)


def _composition_path_is_declared(
    schema: dict[str, object],
    path: str,
) -> bool:
    """Composer path가 JSON Schema의 properties/items에 실제 선언됐는지 본다."""
    current: object = schema
    for raw_segment in path.split("."):
        if not isinstance(current, dict):
            return False
        properties = current.get("properties")
        if not isinstance(properties, dict):
            return False
        name = raw_segment.removesuffix("[*]")
        child = properties.get(name)
        if not isinstance(child, dict):
            return False
        if raw_segment.endswith("[*]"):
            if child.get("type") != "array":
                return False
            child = child.get("items")
            if not isinstance(child, dict):
                return False
        current = child
    return True


def validate_composition_fields(
    value: object,
    *,
    json_schema: dict[str, object] | None = None,
) -> tuple[str, ...]:
    """Contract extension을 bounded·schema-declared path tuple로 검증한다."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(  # noqa: TRY004 - public contract validation API
            f"{COMPOSITION_FIELDS_EXTENSION} must be an array"
        )
    if not value or len(value) > MAX_COMPOSITION_FIELD_PATHS:
        raise ValueError(
            f"{COMPOSITION_FIELDS_EXTENSION} must contain 1.."
            f"{MAX_COMPOSITION_FIELD_PATHS} paths"
        )
    paths: list[str] = []
    for raw_path in value:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("composition field path must be a non-empty string")
        path = raw_path.strip()
        if path != raw_path or not _COMPOSITION_PATH_RE.fullmatch(path):
            raise ValueError(f"invalid composition field path: {raw_path!r}")
        segments = path.split(".")
        if len(segments) > MAX_COMPOSITION_FIELD_DEPTH:
            raise ValueError("composition field path is too deep")
        for segment in segments:
            raw_name = segment.removesuffix("[*]")
            split_name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw_name)
            name = raw_name.casefold()
            components = set(re.split(r"[-_]", split_name.casefold()))
            compact_name = re.sub(r"[-_]", "", name)
            if name.startswith("_") or bool(
                components & _FORBIDDEN_COMPOSITION_SEGMENT_MARKERS
            ) or compact_name in _FORBIDDEN_COMPOSITION_SEGMENT_MARKERS:
                raise ValueError(
                    "composition field path contains a forbidden presentation "
                    f"or private field: {path}"
                )
        if json_schema is not None and not _composition_path_is_declared(
            json_schema, path
        ):
            raise ValueError(
                "composition field path is not declared by JSON Schema: "
                f"{path}"
            )
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise ValueError("composition field paths must be unique")
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class StructuralEvidenceRelationDeclaration:
    """Descriptor가 선언한 domain-neutral evidence relation 조건이다."""

    when_path: str
    when_equals: JsonValue
    evidence_fields: tuple[str, ...]
    evidence_must_be_visible: bool


def validate_structural_evidence_relations(
    value: object,
    *,
    json_schema: dict[str, object],
    composition_fields: tuple[str, ...],
) -> tuple[StructuralEvidenceRelationDeclaration, ...]:
    """Evidence relation을 의미 enum 없이 bounded structural contract로 검증한다."""
    if value is None:
        return ()
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_COMPOSITION_RELATIONS:
        raise ValueError(
            f"{STRUCTURAL_EVIDENCE_RELATIONS_EXTENSION} must contain 1.."
            f"{MAX_COMPOSITION_RELATIONS} relations"
        )
    declarations: list[StructuralEvidenceRelationDeclaration] = []
    seen: dict[tuple[str, str, tuple[str, ...]], bool] = {}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "when",
            "evidence_fields",
            "evidence_must_be_visible",
        }:
            raise ValueError("structural evidence relation has invalid fields")
        when = raw["when"]
        evidence = raw["evidence_fields"]
        evidence_must_be_visible = raw["evidence_must_be_visible"]
        if not isinstance(evidence_must_be_visible, bool):
            raise TypeError(
                "structural evidence visibility policy must be a boolean"
            )
        if not isinstance(when, dict) or set(when) != {"path", "equals"}:
            raise ValueError(
                "structural evidence relation when must contain path and equals"
            )
        when_path = validate_composition_fields(
            [when["path"]],
            json_schema=json_schema,
        )[0]
        if "[*]" in when_path:
            raise ValueError(
                "structural evidence relation condition cannot use a wildcard"
            )
        when_equals = when["equals"]
        if isinstance(when_equals, dict | list):
            raise TypeError(
                "structural evidence relation condition must use a scalar value"
            )
        _validate_json_value(
            when_equals,
            path="structural evidence relation condition",
        )
        evidence_fields = validate_composition_fields(
            evidence,
            json_schema=json_schema,
        )
        if any(path not in composition_fields for path in evidence_fields):
            raise ValueError(
                "structural relation evidence must be composition-visible"
            )
        identity = (
            when_path,
            json.dumps(
                when_equals,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            evidence_fields,
        )
        previous_policy = seen.get(identity)
        if previous_policy is not None:
            if previous_policy == evidence_must_be_visible:
                raise ValueError("duplicate structural evidence relation")
            raise ValueError("conflicting structural evidence relation")
        seen[identity] = evidence_must_be_visible
        declarations.append(
            StructuralEvidenceRelationDeclaration(
                when_path=when_path,
                when_equals=when_equals,
                evidence_fields=evidence_fields,
                evidence_must_be_visible=evidence_must_be_visible,
            )
        )
    return tuple(declarations)


def _validate_json_value(value: Any, *, path: str = "payload") -> None:
    """업무 키를 해석하지 않고 Python 전용 값을 fail-closed로 거부한다."""
    if value is None or isinstance(value, str | bool):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string object key")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains a non-JSON value: {type(value).__name__}")


def _canonicalize_json_object(value: Any) -> str:
    """중첩 변경이 원본 계약에 전파되지 않도록 JSON 객체를 문자열로 고정한다."""
    if not isinstance(value, dict):
        raise TypeError("canonical JSON payload must be an object")
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _restore_json_object(value: str) -> dict[str, JsonValue]:
    """내부 canonical 문자열에서 호출자 전용 방어적 복사본을 복원한다."""
    restored = json.loads(value)
    if not isinstance(restored, dict):  # pragma: no cover - 생성 validator의 불변식
        raise TypeError("canonical JSON payload must decode to an object")
    return restored


CanonicalJsonObject = Annotated[
    str,
    BeforeValidator(_canonicalize_json_object),
    PlainSerializer(_restore_json_object, return_type=dict[str, JsonValue]),
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


class ContractModel(BaseModel):
    """모든 envelope에 불변성과 미지 필드 거부 정책을 공통 적용한다."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class AssetRefV1(ContractModel):
    """자산 종류와 이름으로 구성한 도메인 중립 참조다."""

    type: NonEmptyStr
    name: NonEmptyStr


class AssetBindingRefV1(ContractModel):
    """소유 자산에 귀속된 실행 binding의 고정 참조다."""

    owner_ref: AssetRefV1
    binding_id: NonEmptyStr
    binding_hash: NonEmptyStr


class AttachmentRefV1(ContractModel):
    """요청에 포함된 첨부 파일의 최소 참조 정보다."""

    attachment_id: NonEmptyStr
    media_type: NonEmptyStr
    filename: NonEmptyStr | None = None


class CronSourceV1(ContractModel):
    """cron 요청의 작업과 실행 출처를 식별한다."""

    job_id: NonEmptyStr
    run_id: NonEmptyStr


class RequestEnvelopeV1(ContractModel):
    """외부 요청을 graph에 전달하는 불변 envelope다."""

    schema_version: Literal["request.v1"] = Field(
        default="request.v1", alias="schema"
    )
    request_id: NonEmptyStr
    source: Literal["telegram", "cron", "internal"]
    session_key: NonEmptyStr
    received_at: datetime
    original_text: str
    attachments: tuple[AttachmentRefV1, ...] = ()
    cron: CronSourceV1 | None = None
    deadline_at: datetime | None = None
    locale: NonEmptyStr = "ko-KR"

    @model_validator(mode="after")
    def validate_source_metadata(self) -> RequestEnvelopeV1:
        """cron 메타데이터와 deadline을 출처·수신 시각에 맞게 제한한다."""
        if (self.source == "cron") != (self.cron is not None):
            raise ValueError("cron metadata must be present only for cron requests")
        if self.deadline_at is not None and self.deadline_at <= self.received_at:
            raise ValueError("deadline_at must be later than received_at")
        return self


class ContractRefV1(ContractModel):
    """owner와 schema hash를 함께 고정하는 계약 참조다."""

    contract_id: NonEmptyStr
    version: NonEmptyStr
    owner_ref: AssetRefV1
    schema_hash: NonEmptyStr


class ContractDescriptorV1(ContractModel):
    """계약 참조와 변경 불가능한 canonical JSON Schema를 결합한다."""

    ref: ContractRefV1
    json_schema_json: CanonicalJsonObject = Field(alias="json_schema")
    binding_ref: AssetBindingRefV1 | None = None

    @property
    def json_schema(self) -> dict[str, JsonValue]:
        """호출자의 중첩 변경이 계약 원본에 닿지 않는 schema 복사본을 반환한다."""
        return _restore_json_object(self.json_schema_json)

    @property
    def composition_fields(self) -> tuple[str, ...]:
        """중앙 composer에 노출하도록 계약이 선언한 semantic path만 반환한다."""
        return validate_composition_fields(
            self.json_schema.get(COMPOSITION_FIELDS_EXTENSION),
            json_schema=self.json_schema,
        )

    @property
    def structural_evidence_relations(
        self,
    ) -> tuple[StructuralEvidenceRelationDeclaration, ...]:
        """계약이 선언한 structural evidence relation을 반환한다."""
        schema = self.json_schema
        fields = validate_composition_fields(
            schema.get(COMPOSITION_FIELDS_EXTENSION),
            json_schema=schema,
        )
        return validate_structural_evidence_relations(
            schema.get(STRUCTURAL_EVIDENCE_RELATIONS_EXTENSION),
            json_schema=schema,
            composition_fields=fields,
        )

    @model_validator(mode="after")
    def validate_binding_owner(self) -> ContractDescriptorV1:
        """binding과 contract가 서로 다른 owner를 가리키는 상태를 차단한다."""
        if (
            self.binding_ref is not None
            and self.binding_ref.owner_ref != self.ref.owner_ref
        ):
            raise ValueError("binding owner must match the contract owner")
        schema = self.json_schema
        composition_fields = validate_composition_fields(
            schema.get(COMPOSITION_FIELDS_EXTENSION),
            json_schema=schema,
        )
        validate_structural_evidence_relations(
            schema.get(STRUCTURAL_EVIDENCE_RELATIONS_EXTENSION),
            json_schema=schema,
            composition_fields=composition_fields,
        )
        return self


class RetryPolicyV1(ContractModel):
    """멱등성이 입증된 호출만 재시도하도록 제한하는 정책이다."""

    max_attempts: PositiveInt = 1
    idempotent: bool = False
    retry_timeouts: bool = False

    @model_validator(mode="after")
    def validate_retry_safety(self) -> RetryPolicyV1:
        """비멱등 작업의 복수 시도로 side effect가 중복되지 않게 한다."""
        if self.max_attempts > 1 and not self.idempotent:
            raise ValueError("multiple attempts require an idempotent policy")
        return self


class AssetDefinitionSnapshotV1(ContractModel):
    """계획 시점의 자산 정의와 안전 속성을 원자적으로 고정한다."""

    asset_ref: AssetRefV1
    definition_id: NonEmptyStr
    definition_fingerprint: NonEmptyStr
    input_contract: ContractRefV1 | None = None
    output_contract: ContractRefV1 | None = None
    declared_binding: AssetBindingRefV1 | None = None
    declared: bool
    read_only: bool
    side_effects: bool
    requires_confirmation: bool
    retry_policy: RetryPolicyV1
    fallback_refs: tuple[AssetRefV1, ...] = ()

    @model_validator(mode="after")
    def validate_ownership(self) -> AssetDefinitionSnapshotV1:
        """계약·binding owner와 side-effect 선언의 모순을 거부한다."""
        for ref in (self.input_contract, self.output_contract):
            if ref is not None and ref.owner_ref != self.asset_ref:
                raise ValueError("contract owner must match the snapshot asset")
        if (
            self.declared_binding is not None
            and self.declared_binding.owner_ref != self.asset_ref
        ):
            raise ValueError("binding owner must match the snapshot asset")
        if self.read_only and self.side_effects:
            raise ValueError("a read-only asset cannot declare side effects")
        if self.side_effects and not self.requires_confirmation:
            raise ValueError("side-effect assets must require confirmation")
        return self


class ExecutionBudgetV1(ContractModel):
    """graph 실행의 모든 소비 축에 유한한 상한을 둔다."""

    max_graph_steps: PositiveInt
    max_asset_calls: PositiveInt
    max_llm_calls: PositiveInt
    max_tokens: PositiveInt
    deadline_at: datetime
    max_parallel_invocations: PositiveInt


class AssetInvocationV1(ContractModel):
    """검증된 자산 호출 입력과 hash continuity를 보존하는 envelope다."""

    schema_version: Literal["asset_invocation.v1"] = Field(
        default="asset_invocation.v1", alias="schema"
    )
    invocation_id: NonEmptyStr
    asset_ref: AssetRefV1
    definition_fingerprint: NonEmptyStr
    input_contract: ContractRefV1
    payload_json: CanonicalJsonObject = Field(alias="payload")
    payload_hash: NonEmptyStr
    output_contract: ContractRefV1
    depends_on: tuple[NonEmptyStr, ...] = ()
    fallback_refs: tuple[AssetRefV1, ...] = ()

    @property
    def payload(self) -> dict[str, JsonValue]:
        """내부 canonical payload와 hash를 보호하는 방어적 복사본을 반환한다."""
        return _restore_json_object(self.payload_json)

    @model_validator(mode="after")
    def validate_contract_owners(self) -> AssetInvocationV1:
        """입출력 계약 owner와 invocation dependency의 모순을 차단한다."""
        if self.input_contract.owner_ref != self.asset_ref:
            raise ValueError("input contract owner must match invocation asset")
        if self.output_contract.owner_ref != self.asset_ref:
            raise ValueError("output contract owner must match invocation asset")
        if self.invocation_id in self.depends_on:
            raise ValueError("an invocation cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on entries must be unique")
        return self


class ExecutionPlanV1(ContractModel):
    """자산 호출 DAG와 실행 budget을 묶은 계획 envelope다."""

    schema_version: Literal["execution_plan.v1"] = Field(
        default="execution_plan.v1", alias="schema"
    )
    plan_id: NonEmptyStr
    revision: PositiveInt
    request_id: NonEmptyStr
    catalog_fingerprint: NonEmptyStr
    selected_route: Literal["recipe", "react", "deep_research"]
    invocations: tuple[AssetInvocationV1, ...]
    budget: ExecutionBudgetV1

    @model_validator(mode="after")
    def validate_dependency_dag(self) -> ExecutionPlanV1:
        """알 수 없는 dependency와 순환이 dispatch 단계로 넘어가지 않게 한다."""
        ids = [invocation.invocation_id for invocation in self.invocations]
        if len(ids) != len(set(ids)):
            raise ValueError("invocation_id values must be unique")
        known = set(ids)
        graph = {item.invocation_id: set(item.depends_on) for item in self.invocations}
        if any(not deps <= known for deps in graph.values()):
            raise ValueError("depends_on must reference an invocation in this plan")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            """깊이 우선 탐색으로 현재 경로에 재진입하는 순환을 검출한다."""
            if node in visiting:
                raise ValueError("invocation dependencies must form a DAG")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for invocation_id in ids:
            visit(invocation_id)
        return self


class NormalizedAssetResultV1(ContractModel):
    """자산 결과 payload와 검증된 hash를 함께 운반하는 envelope다."""

    schema_version: Literal["asset_result.v1"] = Field(
        default="asset_result.v1", alias="schema"
    )
    invocation_id: NonEmptyStr
    output_contract: ContractRefV1
    status: AssetResultStatus
    payload_json: CanonicalJsonObject = Field(alias="payload")
    payload_hash: NonEmptyStr
    effect_status: EffectStatus = EffectStatus.NONE

    @property
    def payload(self) -> dict[str, JsonValue]:
        """결과 원본과 hash를 보호하는 payload 방어적 복사본을 반환한다."""
        return _restore_json_object(self.payload_json)


class DraftArtifactV1(ContractModel):
    """최종 확정 전 응답 본문과 outcome을 보존한다."""

    schema_version: Literal["draft_artifact.v1"] = Field(
        default="draft_artifact.v1", alias="schema"
    )
    artifact_id: NonEmptyStr
    request_id: NonEmptyStr
    content: NonEmptyStr
    outcome: TerminalOutcome


class FinalArtifactV1(ContractModel):
    """전송 가능한 최종 본문과 content hash를 고정한다."""

    schema_version: Literal["final_artifact.v1"] = Field(
        default="final_artifact.v1", alias="schema"
    )
    artifact_id: NonEmptyStr
    request_id: NonEmptyStr
    content: NonEmptyStr
    outcome: TerminalOutcome
    content_hash: NonEmptyStr


class DeliveryIntentV1(ContractModel):
    """최종 artifact의 채널 전송 의도와 초기 상태를 표현한다."""

    schema_version: Literal["delivery_intent.v1"] = Field(
        default="delivery_intent.v1", alias="schema"
    )
    delivery_id: NonEmptyStr
    request_id: NonEmptyStr
    artifact_id: NonEmptyStr
    artifact_hash: NonEmptyStr
    channel: Literal["telegram", "cron", "internal"]
    destination_ref: NonEmptyStr
    status: DeliveryStatus = DeliveryStatus.READY
    max_attempts: PositiveInt = 1

    @model_validator(mode="after")
    def validate_initial_status(self) -> DeliveryIntentV1:
        """아직 실행하지 않은 intent가 완료·실패 상태로 시작하지 않게 한다."""
        if self.status not in {DeliveryStatus.READY, DeliveryStatus.SHADOWED}:
            raise ValueError("delivery intent must start ready or shadowed")
        return self
