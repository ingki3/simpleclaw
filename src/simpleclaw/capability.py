"""스킬/레시피 공용 capability metadata 모델과 파서.

BIZ-425 — 케이스별(스포츠/주식/날씨 등) 처리를 Python 라우터 override 로 박지
않고, runtime skill(SKILL.md frontmatter)/recipe(recipe.yaml)의 `capability:`
metadata 로 표현하기 위한 공용 contract 다. skills/recipes 양쪽 로더가 같은
파서를 쓰고, `agent.capability_router` 가 이 metadata 만 보고 read-only
자동 실행 후보를 고른다.

설계 결정:
- metadata 가 없거나 파싱 불가하면 **보수적 기본값**(`read_only=False`,
  `side_effects=True`, `declared=False`)으로 취급한다 — 선언하지 않은 자산이
  자동 실행 후보가 되는 사고를 원천 차단한다.
- 파싱 오류는 해당 자산의 capability 만 기본값으로 떨어뜨리고 경고 로그를
  남긴다. 자산 자체 로드는 막지 않는다(기존 스킬/레시피 무영향).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _canonical_json(value: object, *, source: str) -> str:
    """계약 metadata를 순서와 입력 객체 mutation에 무관한 JSON으로 고정한다."""
    if not isinstance(value, dict):
        raise ValueError(f"{source} must be a JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} must contain only JSON values") from exc


def _sha256(value: str) -> str:
    """Canonical metadata identity를 고정 길이 SHA-256으로 만든다."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnedContractMetadata:
    """Recipe/Skill이 소유하는 versioned JSON Schema 계약 선언."""

    contract_id: str
    version: str
    owner_type: str
    owner_name: str
    schema_json: str
    schema_hash: str

    @property
    def json_schema(self) -> dict[str, Any]:
        """호출자가 원본 metadata를 바꾸지 못하도록 schema 복사본을 반환한다."""
        value = json.loads(self.schema_json)
        if not isinstance(value, dict):  # pragma: no cover - parser 불변식
            raise TypeError("contract schema must decode to an object")
        return value


@dataclass(frozen=True)
class OwnedBindingMetadata:
    """Asset-owned deterministic binding의 opaque identity와 명세."""

    binding_id: str
    owner_type: str
    owner_name: str
    binding_json: str
    binding_hash: str

    @property
    def binding(self) -> dict[str, Any]:
        """Asset adapter만 해석할 수 있는 binding 명세 복사본을 반환한다."""
        value = json.loads(self.binding_json)
        if not isinstance(value, dict):  # pragma: no cover - parser 불변식
            raise TypeError("binding metadata must decode to an object")
        return value


def parse_owned_contract_metadata(
    raw: object,
    *,
    source: str,
) -> OwnedContractMetadata | None:
    """Optional owner-qualified contract를 엄격히 파싱하고 hash를 검증한다."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{source} must be a mapping")
    allowed = {
        "contract_id", "version", "owner_ref", "json_schema", "schema_hash"
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{source} contains unsupported keys: {unknown}")
    owner = raw.get("owner_ref")
    if not isinstance(owner, dict) or set(owner) != {"type", "name"}:
        raise ValueError(f"{source}.owner_ref must contain exactly type and name")
    values = {
        "contract_id": raw.get("contract_id"),
        "version": raw.get("version"),
        "owner_type": owner.get("type"),
        "owner_name": owner.get("name"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError(f"{source} identity fields must be non-empty strings")
    schema_json = _canonical_json(raw.get("json_schema"), source=f"{source}.json_schema")
    schema_hash = _sha256(schema_json)
    declared_hash = raw.get("schema_hash")
    if declared_hash is not None and declared_hash != schema_hash:
        raise ValueError(f"{source}.schema_hash does not match json_schema")
    return OwnedContractMetadata(
        contract_id=str(values["contract_id"]).strip(),
        version=str(values["version"]).strip(),
        owner_type=str(values["owner_type"]).strip(),
        owner_name=str(values["owner_name"]).strip(),
        schema_json=schema_json,
        schema_hash=schema_hash,
    )


def parse_owned_binding_metadata(
    raw: object,
    *,
    source: str,
) -> OwnedBindingMetadata | None:
    """Optional deterministic binding을 엄격히 파싱하고 identity hash를 고정한다."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{source} must be a mapping")
    allowed = {"binding_id", "owner_ref", "binding", "binding_hash"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{source} contains unsupported keys: {unknown}")
    owner = raw.get("owner_ref")
    if not isinstance(owner, dict) or set(owner) != {"type", "name"}:
        raise ValueError(f"{source}.owner_ref must contain exactly type and name")
    values = {
        "binding_id": raw.get("binding_id"),
        "owner_type": owner.get("type"),
        "owner_name": owner.get("name"),
    }
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError(f"{source} identity fields must be non-empty strings")
    binding_json = _canonical_json(raw.get("binding"), source=f"{source}.binding")
    binding_hash = _sha256(binding_json)
    declared_hash = raw.get("binding_hash")
    if declared_hash is not None and declared_hash != binding_hash:
        raise ValueError(f"{source}.binding_hash does not match binding")
    return OwnedBindingMetadata(
        binding_id=str(values["binding_id"]).strip(),
        owner_type=str(values["owner_type"]).strip(),
        owner_name=str(values["owner_name"]).strip(),
        binding_json=binding_json,
        binding_hash=binding_hash,
    )


def require_complete_contract_metadata(
    *,
    input_contract: OwnedContractMetadata | None,
    output_contract: OwnedContractMetadata | None,
    binding: OwnedBindingMetadata | None,
    source: str,
) -> None:
    """Typed V4 metadata가 일부만 선언돼 자동 후보로 승격되는 일을 차단한다."""
    present = tuple(item is not None for item in (input_contract, output_contract, binding))
    if any(present) and not all(present):
        raise ValueError(
            f"{source} must declare input_contract, output_contract, and binding together"
        )


@dataclass(frozen=True)
class CapabilityMetadata:
    """단일 스킬/레시피의 capability 선언.

    Attributes:
        domains: 자산이 다루는 도메인 힌트 (예: sports, market, weather).
        intents: 자산이 해결하는 의도 (예: standings, current_result, quote).
        read_only: 외부 상태를 변경하지 않는 조회 전용인지.
        side_effects: 파일/알림/cron 등 부수효과가 있는지.
        freshness_sensitive: 최신성이 중요한 조회인지.
        direct_answer: 결과만으로 최종 답변 구성이 가능한지 (1차에선 힌트로만 사용).
        requires_confirmation: 실행 전 사용자 확인이 필요한지.
        output_contract: 출력 형식 계약 (예: structured_evidence).
        declared: metadata 가 실제로 선언되었는지 — 미선언 보수 기본값과 구분.
    """

    domains: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    read_only: bool = False
    side_effects: bool = True
    freshness_sensitive: bool = False
    direct_answer: bool = False
    requires_confirmation: bool = False
    output_contract: str | None = None
    coverage: str = "partial_coverage"
    input_contract: str | None = None
    fallback_modes: tuple[str, ...] = ()
    retry_statuses: tuple[str, ...] = ()
    declared: bool = False

    @property
    def safe_for_auto_execution(self) -> bool:
        """자동 실행(사용자 확인 없는 선조회) 후보가 될 수 있는지.

        명시적으로 선언된 read-only + 무부수효과 자산만 허용한다.
        """
        return (
            self.declared
            and self.read_only
            and not self.side_effects
            and not self.requires_confirmation
        )

    @property
    def eligible_for_fast_path(self) -> bool:
        """Typed full-coverage asset만 mode 이전 직접 실행을 허용한다."""
        return (
            self.declared
            and self.coverage == "full_coverage"
            and self.input_contract == "query.v1"
            and self.output_contract == "asset_result.v1"
            and (
                self.safe_for_auto_execution
                or (self.side_effects and self.requires_confirmation)
            )
        )


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    """YAML 리스트/단일 문자열을 소문자 문자열 튜플로 정규화한다."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: list[object] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return ()
    return tuple(
        str(item).strip().lower() for item in items if str(item).strip()
    )


def parse_capability_metadata(
    raw: object, *, source: str = ""
) -> CapabilityMetadata:
    """`capability:` YAML 블록을 :class:`CapabilityMetadata` 로 변환한다.

    Args:
        raw: frontmatter/recipe.yaml 의 ``capability`` 키 값 (보통 dict).
        source: 경고 로그에 남길 출처 (SKILL.md / recipe.yaml 경로).

    Returns:
        파싱된 metadata. ``raw`` 가 None 이면 미선언 보수 기본값,
        매핑이 아니면 경고 후 미선언 보수 기본값.
    """
    if raw is None:
        return CapabilityMetadata()
    if not isinstance(raw, dict):
        logger.warning(
            "Invalid 'capability' block in %s: expected mapping, got %s — "
            "falling back to conservative defaults.",
            source or "<unknown>", type(raw).__name__,
        )
        return CapabilityMetadata()

    output_contract = raw.get("output_contract")
    input_contract = raw.get("input_contract")
    coverage = str(raw.get("coverage") or "partial_coverage").strip().lower()
    if coverage not in {
        "full_coverage",
        "partial_coverage",
        "no_match",
        "ambiguous",
        "needs_input",
        "needs_confirmation",
    }:
        coverage = "partial_coverage"
    return CapabilityMetadata(
        domains=_coerce_str_tuple(raw.get("domains")),
        intents=_coerce_str_tuple(raw.get("intents")),
        read_only=bool(raw.get("read_only", False)),
        # side_effects 미기재 시 True — read_only 만 쓰고 side_effects 를 빠뜨린
        # 선언이 자동 실행 후보가 되지 않도록 명시 선언을 요구한다.
        side_effects=bool(raw.get("side_effects", True)),
        freshness_sensitive=bool(raw.get("freshness_sensitive", False)),
        direct_answer=bool(raw.get("direct_answer", False)),
        requires_confirmation=bool(raw.get("requires_confirmation", False)),
        output_contract=str(output_contract) if output_contract else None,
        coverage=coverage,
        input_contract=str(input_contract) if input_contract else None,
        fallback_modes=_coerce_str_tuple(raw.get("fallback_modes")),
        retry_statuses=_coerce_str_tuple(raw.get("retry_statuses")),
        declared=True,
    )
