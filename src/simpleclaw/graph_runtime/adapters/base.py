"""Generic adapter protocol and normalized dispatch response.

This module deliberately owns only lifecycle, immutable contract identities and
opaque JSON payloads. Concrete Recipe/Skill adapters own their binding formats.
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
    """One Recipe-owned deterministic mapping to a target Skill payload."""

    binding_id: str
    target_skill: str
    target_asset: AssetRefV1
    target_definition_fingerprint: str
    input_contract: ContractRefV1
    payload_json: str
    payload_hash: str
    source_payload_json: str
    source_payload_hash: str

    @property
    def payload(self) -> dict[str, JsonValue]:
        """Return a defensive copy of the canonical child payload."""
        import json

        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("bound payload must decode to an object")
        return value


@dataclass(frozen=True)
class AdapterResponse:
    """Dispatch result that can represent pre-executor fail-closed outcomes."""

    invocation_id: str
    status: AssetResultStatus
    input_payload_hash: str
    effect_status: EffectStatus
    result: NormalizedAssetResultV1 | None = None
    dispatched: bool = False
    receipt_reused: bool = False
    error_code: str | None = None


class GenericAssetAdapter(Protocol):
    """Common async surface consumed by graph dispatch nodes."""

    async def dispatch(
        self,
        invocation: AssetInvocationV1,
        *,
        receipts: tuple[object, ...] = (),
        dispatch_started: bool = False,
        authorized: bool = False,
    ) -> AdapterResponse: ...
