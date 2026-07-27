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

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_STRUCTURED_EVIDENCE_CONTRACT = "structured_evidence"
_STRUCTURED_EVIDENCE_MAX_AGE = timedelta(days=3)
_STRUCTURED_EVIDENCE_FUTURE_TOLERANCE = timedelta(minutes=10)
_EVIDENCE_SOURCE_KEYS = frozenset({"source", "sources", "provider"})
_EVIDENCE_AS_OF_KEYS = frozenset(
    {
        "as_of",
        "as_of_kst",
        "base_date",
        "date",
        "fetched_at",
        "local_traded_at",
        "published_at",
        "timestamp",
        "updated_at",
    }
)
_EVIDENCE_DATA_CONTAINER_KEYS = frozenset(
    {"data", "facts", "items", "records", "results", "rows", "values"}
)
_EVIDENCE_METADATA_KEYS = (
    _EVIDENCE_SOURCE_KEYS
    | _EVIDENCE_AS_OF_KEYS
    | frozenset(
        {
            "confidence",
            "error",
            "freshness",
            "kind",
            "limitations",
            "note",
            "notes",
            "reason",
            "source_priority",
            "stale",
            "status",
            "success",
            "valid",
        }
    )
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
    def provides_fresh_structured_evidence(self) -> bool:
        """실시간 근거 제공자로 사용할 수 있는지 metadata만으로 보수 판정한다.

        단순히 최신성이나 출력 형식 중 하나만 선언한 자산은 허용하지 않는다.
        사용자 확인 없이 실행 가능한 read-only/no-side-effect 자산이면서
        ``structured_evidence`` 계약을 명시한 경우에만 후보가 된다.
        """
        return (
            self.safe_for_auto_execution
            and self.freshness_sensitive
            and (self.output_contract or "").strip().lower()
            == _STRUCTURED_EVIDENCE_CONTRACT
        )


def _iter_mapping_values(
    value: object,
    *,
    keys: frozenset[str],
) -> list[object]:
    """중첩 JSON에서 지정 키의 값을 안전하게 수집한다."""
    found: list[object] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            if key in keys:
                found.append(child)
            found.extend(_iter_mapping_values(child, keys=keys))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_iter_mapping_values(child, keys=keys))
    return found


def _iter_mappings(value: object) -> list[dict[str, Any]]:
    """중첩 JSON의 모든 mapping을 evidence record 후보로 펼친다."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_iter_mappings(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_iter_mappings(child))
    return found


def _has_nonempty_contract_value(value: object) -> bool:
    """source/data 후보가 비어 있지 않은지 재귀적으로 확인한다."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _parse_evidence_datetime(value: object) -> datetime | None:
    """ISO datetime/date를 UTC-aware datetime으로 정규화한다."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw)
        except ValueError:
            return None
        parsed = datetime.combine(parsed_date, time.min)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _has_meaningful_evidence_data(payload: dict[str, Any]) -> bool:
    """동일 record에 source/as_of 외 직접 근거 데이터가 있는지 확인한다.

    중첩 mapping 자체는 데이터로 세지 않는다. 그래야 상위 envelope의 source와
    날짜를 서로 다른 하위 section의 값에 결합하지 않는다. 반면 facts/rows처럼
    record가 직접 소유한 list와 scalar 값은 근거 데이터로 인정한다.
    """
    return any(
        (
            str(key).strip().lower() in _EVIDENCE_DATA_CONTAINER_KEYS
            or (
                str(key).strip().lower() not in _EVIDENCE_METADATA_KEYS
                and not isinstance(value, dict)
            )
        )
        and _has_nonempty_contract_value(value)
        for key, value in payload.items()
    )


def _has_rejected_evidence_state(payload: dict[str, Any]) -> bool:
    """단일 record의 명시 오류·무효·stale 상태를 fail-closed 판정한다."""
    status = str(payload.get("status") or "").strip().lower()
    freshness = str(payload.get("freshness") or "").strip().lower()
    return (
        payload.get("success") is False
        or payload.get("valid") is False
        or payload.get("stale") is True
        or status in {"error", "failed", "failure", "invalid", "stale"}
        or freshness in {"expired", "invalid", "stale"}
        or _has_nonempty_contract_value(payload.get("error"))
    )


def has_usable_structured_evidence(
    payload: object,
    *,
    now: datetime | None = None,
    max_age: timedelta = _STRUCTURED_EVIDENCE_MAX_AGE,
) -> bool:
    """일반 capability skill의 structured evidence 결과 계약을 검증한다.

    이름이나 문자열 길이는 근거가 아니다. JSON object가 source, fresh as-of,
    실제 데이터와 함께 와야 하며 명시 오류/stale/invalid 표시는 즉시 거부한다.
    중첩 source/date를 허용하는 이유는 시장 summary처럼 섹션별 출처와 기준일을
    보존하는 기존 도메인 출력 형식을 wrapper 재작성 없이 수용하기 위해서다.
    """
    if not isinstance(payload, dict) or not payload:
        return False

    records = _iter_mappings(payload)
    if any(_has_rejected_evidence_state(record) for record in records):
        return False

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    usable_record_seen = False
    for record in records:
        source_values = [
            value
            for raw_key, value in record.items()
            if str(raw_key).strip().lower() in _EVIDENCE_SOURCE_KEYS
        ]
        timestamp_values = [
            value
            for raw_key, value in record.items()
            if str(raw_key).strip().lower() in _EVIDENCE_AS_OF_KEYS
        ]
        if (
            not any(
                _has_nonempty_contract_value(value)
                for value in source_values
            )
            or not timestamp_values
            or not _has_meaningful_evidence_data(record)
        ):
            continue

        timestamps = [
            _parse_evidence_datetime(value) for value in timestamp_values
        ]
        if any(timestamp is None for timestamp in timestamps):
            return False
        if any(
            not (
                -_STRUCTURED_EVIDENCE_FUTURE_TOLERANCE
                <= current - timestamp
                <= max_age
            )
            for timestamp in timestamps
            if timestamp is not None
        ):
            return False
        usable_record_seen = True

    return usable_record_seen


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
        declared=True,
    )
