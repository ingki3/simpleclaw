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

logger = logging.getLogger(__name__)


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
