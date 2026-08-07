"""공통 adapter protocol과 정규화된 dispatch 응답을 정의한다.

이 모듈은 lifecycle, 불변 contract identity, opaque JSON payload만 소유한다.
구체적인 binding 형식은 Recipe와 Skill adapter가 각각 소유한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from ..contracts import (
    AssetInvocationV1,
    AssetRefV1,
    ContractRefV1,
    NormalizedAssetResultV1,
)
from ..status import AssetResultStatus, EffectStatus


@dataclass(frozen=True)
class BoundSkillPayload:
    """Recipe가 소유한 결정적 mapping으로 만든 단일 Skill payload."""

    binding_id: str
    target_skill: str
    target_asset: AssetRefV1
    target_definition_fingerprint: str
    input_contract: ContractRefV1
    payload_json: str
    payload_hash: str
    source_payload_json: str
    source_payload_hash: str
    constraints_json: str = "{}"
    constraints_hash: str = ""

    @property
    def payload(self) -> dict[str, JsonValue]:
        """호출자가 canonical 원본을 변경하지 못하도록 자식 payload 복사본을 반환한다."""
        import json

        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # pragma: no cover - 생성자 불변식
            raise TypeError("bound payload must decode to an object")
        return value

    @property
    def constraints(self) -> dict[str, JsonValue]:
        """Recipe-owned query constraint의 결정적 sidecar 복사본을 반환한다."""
        import json

        value = json.loads(self.constraints_json)
        if not isinstance(value, dict):  # pragma: no cover - 생성자 불변식
            raise TypeError("bound constraints must decode to an object")
        return value


@dataclass(frozen=True)
class AdapterResponse:
    """executor 호출 전 fail-closed 결과까지 표현하는 dispatch 응답."""

    invocation_id: str
    status: AssetResultStatus
    input_payload_hash: str
    effect_status: EffectStatus
    result: NormalizedAssetResultV1 | None = None
    dispatched: bool = False
    receipt_reused: bool = False
    error_code: str | None = None


class GenericAssetAdapter(Protocol):
    """graph dispatch node가 자산 종류와 무관하게 사용하는 비동기 표면."""

    async def dispatch(
        self,
        invocation: AssetInvocationV1,
        *,
        receipts: tuple[object, ...] = (),
        dispatch_started: bool = False,
        authorized: bool = False,
    ) -> AdapterResponse: ...
