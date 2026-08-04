"""Strict, domain-neutral data contracts for the LangGraph V4 runtime."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .status import AssetResultStatus, DeliveryStatus, EffectStatus, TerminalOutcome

NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


def _validate_json_value(value: Any, *, path: str = "payload") -> None:
    """Reject Python-only values while leaving JSON object keys fully opaque."""
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


class ContractModel(BaseModel):
    """Common policy: immutable envelopes and fail-closed unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AssetRefV1(ContractModel):
    type: NonEmptyStr
    name: NonEmptyStr


class AssetBindingRefV1(ContractModel):
    owner_ref: AssetRefV1
    binding_id: NonEmptyStr
    binding_hash: NonEmptyStr


class AttachmentRefV1(ContractModel):
    attachment_id: NonEmptyStr
    media_type: NonEmptyStr
    filename: NonEmptyStr | None = None


class CronSourceV1(ContractModel):
    job_id: NonEmptyStr
    run_id: NonEmptyStr


class RequestEnvelopeV1(ContractModel):
    schema: Literal["request.v1"] = "request.v1"
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
        if (self.source == "cron") != (self.cron is not None):
            raise ValueError("cron metadata must be present only for cron requests")
        if self.deadline_at is not None and self.deadline_at <= self.received_at:
            raise ValueError("deadline_at must be later than received_at")
        return self


class ContractRefV1(ContractModel):
    contract_id: NonEmptyStr
    version: NonEmptyStr
    owner_ref: AssetRefV1
    schema_hash: NonEmptyStr


class ContractDescriptorV1(ContractModel):
    ref: ContractRefV1
    json_schema: dict[str, JsonValue]
    binding_ref: AssetBindingRefV1 | None = None

    @field_validator("json_schema")
    @classmethod
    def validate_json_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_json_value(value, path="json_schema")
        return value

    @model_validator(mode="after")
    def validate_binding_owner(self) -> ContractDescriptorV1:
        if (
            self.binding_ref is not None
            and self.binding_ref.owner_ref != self.ref.owner_ref
        ):
            raise ValueError("binding owner must match the contract owner")
        return self


class RetryPolicyV1(ContractModel):
    max_attempts: PositiveInt = 1
    idempotent: bool = False
    retry_timeouts: bool = False

    @model_validator(mode="after")
    def validate_retry_safety(self) -> RetryPolicyV1:
        if self.max_attempts > 1 and not self.idempotent:
            raise ValueError("multiple attempts require an idempotent policy")
        return self


class AssetDefinitionSnapshotV1(ContractModel):
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
    max_graph_steps: PositiveInt
    max_asset_calls: PositiveInt
    max_llm_calls: PositiveInt
    max_tokens: PositiveInt
    deadline_at: datetime
    max_parallel_invocations: PositiveInt


class AssetInvocationV1(ContractModel):
    schema: Literal["asset_invocation.v1"] = "asset_invocation.v1"
    invocation_id: NonEmptyStr
    asset_ref: AssetRefV1
    definition_fingerprint: NonEmptyStr
    input_contract: ContractRefV1
    payload: dict[str, JsonValue]
    payload_hash: NonEmptyStr
    output_contract: ContractRefV1
    depends_on: tuple[NonEmptyStr, ...] = ()
    fallback_refs: tuple[AssetRefV1, ...] = ()

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_json_value(value)
        return value

    @model_validator(mode="after")
    def validate_contract_owners(self) -> AssetInvocationV1:
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
    schema: Literal["execution_plan.v1"] = "execution_plan.v1"
    plan_id: NonEmptyStr
    revision: PositiveInt
    request_id: NonEmptyStr
    catalog_fingerprint: NonEmptyStr
    selected_route: Literal["recipe", "react", "deep_research"]
    invocations: tuple[AssetInvocationV1, ...]
    budget: ExecutionBudgetV1

    @model_validator(mode="after")
    def validate_dependency_dag(self) -> ExecutionPlanV1:
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
    schema: Literal["asset_result.v1"] = "asset_result.v1"
    invocation_id: NonEmptyStr
    output_contract: ContractRefV1
    status: AssetResultStatus
    payload: dict[str, JsonValue]
    payload_hash: NonEmptyStr
    effect_status: EffectStatus = EffectStatus.NONE

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_json_value(value)
        return value


class DraftArtifactV1(ContractModel):
    schema: Literal["draft_artifact.v1"] = "draft_artifact.v1"
    artifact_id: NonEmptyStr
    request_id: NonEmptyStr
    content: NonEmptyStr
    outcome: TerminalOutcome


class FinalArtifactV1(ContractModel):
    schema: Literal["final_artifact.v1"] = "final_artifact.v1"
    artifact_id: NonEmptyStr
    request_id: NonEmptyStr
    content: NonEmptyStr
    outcome: TerminalOutcome
    content_hash: NonEmptyStr


class DeliveryIntentV1(ContractModel):
    schema: Literal["delivery_intent.v1"] = "delivery_intent.v1"
    delivery_id: NonEmptyStr
    artifact_id: NonEmptyStr
    channel: Literal["telegram", "cron", "internal"]
    destination_ref: NonEmptyStr
    status: DeliveryStatus = DeliveryStatus.READY
    max_attempts: PositiveInt = 1

    @model_validator(mode="after")
    def validate_initial_status(self) -> DeliveryIntentV1:
        if self.status not in {DeliveryStatus.READY, DeliveryStatus.SHADOWED}:
            raise ValueError("delivery intent must start ready or shadowed")
        return self
