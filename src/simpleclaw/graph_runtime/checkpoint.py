"""V4 clarification/confirmation interrupt와 stale-resume guard."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CheckpointContractError(ValueError):
    """resume가 저장된 exact control point와 일치하지 않는다."""


DEFAULT_GRAPH_CHECKPOINT_PATH = Path(
    "~/.simpleclaw-agent/default/graph-checkpoints.sqlite3"
)


class CheckpointPathIsolationError(ValueError):
    """graph checkpoint가 기존 operational DB와 같은 파일을 가리킨다."""


def resolve_checkpoint_path(
    path: str | Path = DEFAULT_GRAPH_CHECKPOINT_PATH,
    *,
    daemon_db_path: str | Path | None = None,
    conversations_db_path: str | Path | None = None,
) -> Path:
    """checkpoint 경로를 정규화하고 daemon/conversation DB 혼용을 거부한다."""
    resolved = Path(path).expanduser().resolve()
    forbidden = {
        Path(candidate).expanduser().resolve()
        for candidate in (daemon_db_path, conversations_db_path)
        if candidate is not None
    }
    if resolved in forbidden:
        raise CheckpointPathIsolationError(
            "graph checkpoint must use a file separate from daemon/conversations DB"
        )
    return resolved


class InterruptModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class ChoiceV1(InterruptModel):
    choice_id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class InterruptRequestV1(InterruptModel):
    schema_version: Literal["interrupt.v1"] = Field(
        default="interrupt.v1", alias="schema"
    )
    interrupt_id: str = Field(min_length=1)
    kind: Literal["clarification", "confirmation"]
    question: str = Field(min_length=1)
    choices: tuple[ChoiceV1, ...] = ()
    allow_free_text: bool = False
    resume_node: Literal["recipe", "react", "deep_research"]
    checkpoint_thread_id: str = Field(min_length=1)
    checkpoint_version: int = Field(ge=0)
    contract_version: str = Field(min_length=1)
    contract_schema_hash: str = Field(min_length=1)
    catalog_fingerprint: str = Field(min_length=1)
    plan_id: str | None = None
    plan_revision: int | None = Field(default=None, gt=0)
    invocation_id: str | None = None
    payload_hash: str | None = None
    definition_fingerprint: str | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_kind_contract(self) -> InterruptRequestV1:
        if self.kind == "confirmation":
            required = (
                self.plan_id,
                self.plan_revision,
                self.invocation_id,
                self.payload_hash,
                self.definition_fingerprint,
            )
            if any(value is None for value in required):
                raise ValueError("confirmation requires exact plan and payload identity")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        return self


class UserDecisionV1(InterruptModel):
    schema_version: Literal["user_decision.v1"] = Field(
        default="user_decision.v1", alias="schema"
    )
    interrupt_id: str = Field(min_length=1)
    choice_id: str | None = None
    text: str | None = None
    confirmed: bool | None = None
    checkpoint_thread_id: str = Field(min_length=1)
    checkpoint_version: int = Field(ge=0)
    contract_version: str = Field(min_length=1)
    contract_schema_hash: str = Field(min_length=1)
    catalog_fingerprint: str = Field(min_length=1)
    plan_id: str | None = None
    plan_revision: int | None = Field(default=None, gt=0)
    invocation_id: str | None = None
    payload_hash: str | None = None
    definition_fingerprint: str | None = None

    @model_validator(mode="after")
    def require_decision(self) -> UserDecisionV1:
        if self.choice_id is None and self.text is None and self.confirmed is None:
            raise ValueError("a resume decision must contain an answer")
        return self


class ResumeControlV1(InterruptModel):
    """검증 후 node가 적용할 수 있는 최소 resume 명령이다."""

    kind: Literal["clarification", "confirmation"]
    resume_node: Literal["recipe", "react", "deep_research"]
    plan_id: str | None
    previous_revision: int | None
    next_revision: int | None
    authorized: bool | None
    payload_hash: str | None
    answer: str | None


def validate_resume(
    request: InterruptRequestV1,
    decision: UserDecisionV1,
    *,
    now: datetime | None = None,
) -> ResumeControlV1:
    """stale checkpoint/catalog/contract를 dispatch 전에 fail-closed로 거부한다."""
    exact_fields = (
        "interrupt_id",
        "checkpoint_thread_id",
        "checkpoint_version",
        "contract_version",
        "contract_schema_hash",
        "catalog_fingerprint",
    )
    for field_name in exact_fields:
        if getattr(request, field_name) != getattr(decision, field_name):
            raise CheckpointContractError(f"stale resume {field_name}")

    current_time = now or datetime.now(UTC)
    if request.expires_at is not None and current_time >= request.expires_at:
        raise CheckpointContractError("interrupt has expired")

    if request.kind == "confirmation":
        confirmation_fields = (
            "plan_id",
            "plan_revision",
            "invocation_id",
            "payload_hash",
            "definition_fingerprint",
        )
        for field_name in confirmation_fields:
            if getattr(request, field_name) != getattr(decision, field_name):
                raise CheckpointContractError(
                    f"confirmation identity drift: {field_name}"
                )
        if decision.confirmed is None:
            raise CheckpointContractError("confirmation requires confirmed boolean")
        return ResumeControlV1(
            kind=request.kind,
            resume_node=request.resume_node,
            plan_id=request.plan_id,
            previous_revision=request.plan_revision,
            next_revision=request.plan_revision,
            authorized=decision.confirmed,
            payload_hash=request.payload_hash,
            answer=None,
        )

    if decision.confirmed is not None:
        raise CheckpointContractError("clarification cannot authorize an invocation")
    if decision.text is not None and decision.choice_id is not None:
        raise CheckpointContractError(
            "clarification must contain either text or a choice, not both"
        )
    if decision.choice_id is not None:
        allowed_choice_ids = {choice.choice_id for choice in request.choices}
        if decision.choice_id not in allowed_choice_ids:
            raise CheckpointContractError("clarification choice is not allowed")
        answer = decision.choice_id
    elif decision.text is not None:
        if not request.allow_free_text:
            raise CheckpointContractError("clarification does not allow free text")
        answer = decision.text
    else:
        raise CheckpointContractError("clarification requires text or a choice")
    next_revision = 1 if request.plan_revision is None else request.plan_revision + 1
    return ResumeControlV1(
        kind=request.kind,
        resume_node=request.resume_node,
        plan_id=request.plan_id,
        previous_revision=request.plan_revision,
        next_revision=next_revision,
        authorized=None,
        payload_hash=request.payload_hash,
        answer=answer,
    )
