"""Typed request/result boundary for realtime source adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from simpleclaw.agent.turn_plan import FactEntity, UnifiedTurnPlan


class LookupStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNUSABLE = "unusable"


@dataclass(frozen=True)
class RealtimeLookupRequest:
    """Semantic source-selection data produced once by UnifiedTurnPlanner."""

    query: str
    domain: str
    intents: tuple[str, ...]
    entities: tuple[FactEntity, ...]
    reference_date: str
    required_claims: tuple[str, ...]
    as_of_kst: str
    freshness_required: bool = True

    @classmethod
    def from_plan(
        cls,
        plan: UnifiedTurnPlan,
        *,
        as_of_kst: str,
    ) -> RealtimeLookupRequest:
        fact = plan.fact_check
        if not fact.required:
            raise ValueError("realtime lookup requires fact_check.required=true")
        return cls(
            query=fact.search_query or plan.context.standalone_question,
            domain=fact.domain,
            intents=fact.intents,
            entities=fact.entities,
            reference_date=fact.reference_date,
            required_claims=fact.required_claims,
            as_of_kst=as_of_kst,
            freshness_required=fact.freshness_required,
        )

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, object],
    ) -> RealtimeLookupRequest:
        required_fields = {
            "query",
            "domain",
            "intents",
            "entities",
            "reference_date",
            "required_claims",
            "as_of_kst",
        }
        missing = required_fields - set(payload)
        if missing:
            raise ValueError(
                "realtime lookup payload missing structured fields: "
                + ",".join(sorted(missing))
            )
        intents_raw = payload["intents"]
        entities_raw = payload["entities"]
        claims_raw = payload["required_claims"]
        if not isinstance(intents_raw, list | tuple):
            raise TypeError("realtime lookup intents must be an array")
        if not isinstance(entities_raw, list | tuple):
            raise TypeError("realtime lookup entities must be an array")
        if not isinstance(claims_raw, list | tuple):
            raise TypeError("realtime lookup required_claims must be an array")
        entities: list[FactEntity] = []
        for raw in entities_raw:
            if not isinstance(raw, Mapping):
                raise TypeError("realtime lookup entity must be an object")
            kind = str(raw.get("kind") or "").strip()
            value = str(raw.get("value") or "").strip()
            if not kind or not value:
                raise ValueError("realtime lookup entity requires kind and value")
            entities.append(FactEntity(kind=kind, value=value))
        request = cls(
            query=str(payload["query"] or "").strip(),
            domain=str(payload["domain"] or "").strip(),
            intents=tuple(str(item).strip() for item in intents_raw if str(item).strip()),
            entities=tuple(entities),
            reference_date=str(payload["reference_date"] or "").strip(),
            required_claims=tuple(
                str(item).strip() for item in claims_raw if str(item).strip()
            ),
            as_of_kst=str(payload["as_of_kst"] or "").strip(),
            freshness_required=bool(payload.get("freshness_required", True)),
        )
        if not request.query or not request.domain or not request.intents:
            raise ValueError(
                "realtime lookup requires query, domain, and intents"
            )
        return request

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "domain": self.domain,
            "intents": list(self.intents),
            "entities": [
                {"kind": item.kind, "value": item.value}
                for item in self.entities
            ],
            "reference_date": self.reference_date,
            "required_claims": list(self.required_claims),
            "as_of_kst": self.as_of_kst,
            "freshness_required": self.freshness_required,
        }

    def entity(self, kind: str) -> str:
        return next(
            (item.value for item in self.entities if item.kind == kind),
            "",
        )


@dataclass(frozen=True)
class RealtimeLookupResult:
    """Typed outcome plus the provider-compatible evidence payload."""

    request: RealtimeLookupRequest
    status: LookupStatus
    evidence: tuple[dict[str, Any], ...]
    facts: tuple[dict[str, Any], ...]
    limitations: tuple[str, ...]
    payload: Mapping[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return dict(self.payload)
