"""중앙 최종 발화 composer의 검증된 입력과 구조화 draft 계약."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from simpleclaw.graph_runtime.contracts import (
    AssetRefV1,
    CanonicalJsonObject,
    ContractModel,
    NonEmptyStr,
    _restore_json_object,
)
from simpleclaw.graph_runtime.status import AssetResultStatus, EffectStatus


class StructuralEvidenceRelationV1(ContractModel):
    """Descriptor 조건으로 활성화된 구조적 evidence/citation 계약이다."""

    evidence_paths: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=64)
    identity_paths: tuple[NonEmptyStr, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_evidence_paths(self) -> StructuralEvidenceRelationV1:
        if len(set(self.evidence_paths)) != len(self.evidence_paths):
            raise ValueError("structural relation evidence_paths must be unique")
        if len(set(self.identity_paths)) != len(self.identity_paths):
            raise ValueError("structural relation identity_paths must be unique")
        if not set(self.identity_paths) <= set(self.evidence_paths):
            raise ValueError("structural relation identity must be required evidence")
        return self


class CompositionInputV1(ContractModel):
    """계약 allowlist를 통과한 사실만 composer에 전달하는 불변 입력이다."""

    schema_version: Literal["composition_input.v1"] = Field(
        default="composition_input.v1", alias="schema"
    )
    request_id: NonEmptyStr
    question: str = Field(min_length=1, max_length=12_000)
    locale: NonEmptyStr
    selected_route: Literal["recipe", "react", "deep_research"]
    asset_ref: AssetRefV1
    result_status: AssetResultStatus
    effect_status: EffectStatus
    normalized_payload_hash: NonEmptyStr
    public_facts_json: CanonicalJsonObject = Field(alias="public_facts")
    resolved_claims: tuple[NonEmptyStr, ...] = ()
    unresolved_claims: tuple[NonEmptyStr, ...] = ()
    composition_list_root: NonEmptyStr | None = None
    structural_evidence_relations: tuple[StructuralEvidenceRelationV1, ...] = ()

    @property
    def public_facts(self) -> dict[str, JsonValue]:
        """호출자가 nested fact snapshot을 변경하지 못하도록 방어 복사한다."""
        return _restore_json_object(self.public_facts_json)

    @model_validator(mode="after")
    def validate_safe_status(self) -> CompositionInputV1:
        """unsafe result가 composer 호출까지 도달하지 않도록 조기에 거부한다."""
        if self.result_status is not AssetResultStatus.RESOLVED:
            raise ValueError("composition input requires a resolved result")
        if self.effect_status not in {EffectStatus.NONE, EffectStatus.VERIFIED}:
            raise ValueError("composition input requires a safe effect status")
        if not self.public_facts:
            raise ValueError("composition input public_facts must not be empty")
        if len(self.structural_evidence_relations) > 1:
            raise ValueError("conflicting active structural evidence relations")
        return self


class CompositionRenderSegmentV1(ContractModel):
    """Provider가 concrete path 뒤에 붙일 bounded grammar만 표현한다."""

    path: NonEmptyStr
    connector: Literal[
        "space",
        "comma_space",
        "middle_dot_space",
        "semicolon_space",
        "period",
        "question_mark",
        "topic_eun_space",
        "topic_neun_space",
        "subject_i_space",
        "subject_ga_space",
        "object_eul_space",
        "object_reul_space",
        "and_wa_space",
        "and_gwa_space",
        "also_do_space",
        "to_ro_space",
        "at_e_space",
        "copula_imyeo_space",
        "copula_igo_space",
        "polite_copula_period",
        "polite_become_period",
        "english_and_space",
        "english_is_space",
        "english_are_space",
        "english_with_space",
        "english_respectively_period",
        "limitation_uncertain_period",
        "limitation_unverified_period",
        "limitation_korean_uncertain_period",
    ]


class CompositionRenderPlanV1(ContractModel):
    """Provider-authored final 본문 없이 path와 grammar만 선택하는 계약이다."""

    schema_version: Literal["composition_render_plan.v1"] = Field(
        default="composition_render_plan.v1", alias="schema"
    )
    segments: tuple[CompositionRenderSegmentV1, ...] = Field(
        min_length=1,
        max_length=128,
    )
    limitation_paths: tuple[NonEmptyStr, ...] = Field(default=(), max_length=64)


class DraftResponseV1(ContractModel):
    """중앙 composer가 반환하는 자연어 본문과 grounding 경로다."""

    schema_version: Literal["draft_response.v1"] = Field(
        default="draft_response.v1", alias="schema"
    )
    content: str = Field(min_length=1, max_length=3_500)
    cited_paths: tuple[NonEmptyStr, ...] = Field(min_length=1, max_length=128)
    limitation_paths: tuple[NonEmptyStr, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_paths(self) -> DraftResponseV1:
        """중복 path로 guard 검사를 우회하거나 출력 크기를 키우지 못하게 한다."""
        if len(set(self.cited_paths)) != len(self.cited_paths):
            raise ValueError("cited_paths must be unique")
        if len(set(self.limitation_paths)) != len(self.limitation_paths):
            raise ValueError("limitation_paths must be unique")
        if len(self.cited_paths) > 128 or len(self.limitation_paths) > 64:
            raise ValueError("draft response contains too many paths")
        return self
