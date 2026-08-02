"""BIZ-523 — typed realtime request preserves planner semantics."""

from __future__ import annotations

import pytest

from simpleclaw.agent.turn_plan import FactEntity
from simpleclaw.skills.realtime_contracts import (
    LookupStatus,
    RealtimeLookupRequest,
    RealtimeLookupResult,
)
from simpleclaw.skills.realtime_lookup import lookup_async


def _lpga_request() -> RealtimeLookupRequest:
    return RealtimeLookupRequest(
        query="어제 유해란 LPGA 1라운드 성적 확인해줘",
        domain="sports",
        intents=("current_result",),
        entities=(
            FactEntity("athlete", "유해란"),
            FactEntity("league", "LPGA"),
            FactEntity("sport", "golf"),
            FactEntity("round", "1"),
        ),
        reference_date="2026-07-30",
        required_claims=("1라운드 스코어", "순위"),
        as_of_kst="2026-07-31T08:32:15+09:00",
    )


def test_request_payload_round_trip_preserves_all_semantic_fields() -> None:
    request = _lpga_request()
    restored = RealtimeLookupRequest.from_payload(request.to_payload())
    assert restored == request
    assert restored.entity("league") == "LPGA"
    assert restored.intents == ("current_result",)
    assert restored.reference_date == "2026-07-30"


@pytest.mark.asyncio
async def test_lpga_request_is_unsupported_without_kbo_or_query_reclassification() -> None:
    network_calls: list[str] = []

    async def fetch_page(url: str) -> str:
        network_calls.append(url)
        return ""

    result = await lookup_async(_lpga_request(), fetch_page=fetch_page)
    assert isinstance(result, RealtimeLookupResult)
    assert result.request.domain == "sports"
    assert result.request.entity("league") == "LPGA"
    assert result.status is LookupStatus.UNSUPPORTED
    assert result.status is not LookupStatus.NOT_FOUND
    assert network_calls == []


@pytest.mark.asyncio
async def test_query_only_payload_fails_closed_as_unsupported() -> None:
    result = await lookup_async(
        {"query": "어제 유해란 LPGA 성적"},
        fetch_page=lambda _url: None,  # type: ignore[arg-type]
    )
    assert isinstance(result, dict)
    assert result["lookup_status"] == "unsupported"
