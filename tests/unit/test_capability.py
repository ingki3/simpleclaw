"""Capability 기반 fresh structured evidence 계약 테스트."""

from datetime import UTC, datetime, timedelta

from simpleclaw.capability import (
    CapabilityMetadata,
    has_usable_structured_evidence,
)


def _market_capability(**overrides) -> CapabilityMetadata:
    """시장 evidence provider의 보수적 기본 선언을 만든다."""
    values = {
        "domains": ("market",),
        "read_only": True,
        "side_effects": False,
        "freshness_sensitive": True,
        "output_contract": "structured_evidence",
        "declared": True,
    }
    values.update(overrides)
    return CapabilityMetadata(**values)


def test_declared_capability_can_provide_fresh_structured_evidence():
    """스킬 이름과 무관하게 metadata 전체 계약이 provider 자격을 결정한다."""
    assert _market_capability().provides_fresh_structured_evidence is True


def test_undeclared_or_non_structured_capability_fails_closed():
    """미선언·부수효과·비구조화 capability는 evidence provider가 아니다."""
    assert CapabilityMetadata().provides_fresh_structured_evidence is False
    assert _market_capability(side_effects=True).provides_fresh_structured_evidence is False
    assert (
        _market_capability(output_contract="narrative_context")
        .provides_fresh_structured_evidence
        is False
    )


def test_structured_evidence_requires_source_and_fresh_as_of():
    """정상 source/as_of와 실제 데이터가 함께 있을 때만 근거로 인정한다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)
    payload = {
        "source": "Naver m.stock",
        "as_of": "2026-07-27T05:55:00+00:00",
        "facts": [{"symbol": "KOSPI", "value": 6729.46}],
    }

    assert has_usable_structured_evidence(payload, now=now) is True


def test_market_summary_nested_source_and_base_date_satisfy_contract():
    """실제 market-summary의 nested source/base_date 형태도 as-of 계약으로 읽는다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)
    payload = {
        "provider": "kr-stock-skill",
        "base_date": "2026-07-27",
        "realtime_indices": {
            "KOSPI": {
                "status": "available",
                "source": "Naver m.stock",
                "value": 6729.46,
                "local_traded_at": "2026-07-27T14:59:00+09:00",
            }
        },
    }

    assert has_usable_structured_evidence(payload, now=now) is True


def test_structured_evidence_rejects_empty_error_and_stale_contracts():
    """빈 값, 명시 오류, stale/invalid timestamp는 fail-closed다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)
    stale = (now - timedelta(days=10)).isoformat()

    assert has_usable_structured_evidence({}, now=now) is False
    assert has_usable_structured_evidence(
        {"source": "Naver", "as_of": now.isoformat(), "error": "upstream failed"},
        now=now,
    ) is False
    assert has_usable_structured_evidence(
        {
            "source": "Naver",
            "as_of": stale,
            "facts": [{"symbol": "KOSPI", "value": 1}],
        },
        now=now,
    ) is False
    assert has_usable_structured_evidence(
        {
            "source": "Naver",
            "as_of": now.isoformat(),
            "stale": True,
            "facts": [{"symbol": "KOSPI", "value": 1}],
        },
        now=now,
    ) is False


def test_structured_evidence_rejects_nested_error_contract():
    """중첩 section의 명시 오류도 top-level 오류와 동일하게 fail-closed다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)
    payload = {
        "provider": "kr-stock-skill",
        "base_date": "2026-07-27",
        "market": {
            "status": "error",
            "error": "upstream failed",
        },
        "note": "generated",
    }

    assert has_usable_structured_evidence(payload, now=now) is False


def test_structured_evidence_does_not_mix_fresh_and_stale_sections():
    """서로 다른 section의 fresh timestamp와 stale 시장 값을 조합하지 않는다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)
    payload = {
        "news": {
            "source": "Google News RSS",
            "published_at": now.isoformat(),
            "headlines": ["fresh narrative"],
        },
        "market": {
            "source": "Naver m.stock",
            "as_of": (now - timedelta(days=10)).isoformat(),
            "facts": [{"symbol": "KOSPI", "value": 6729.46}],
        },
    }

    assert has_usable_structured_evidence(payload, now=now) is False


def test_structured_evidence_requires_coherent_record():
    """source, as-of, data가 서로 다른 section이면 evidence로 조합하지 않는다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)
    payload = {
        "source_section": {"source": "Naver m.stock"},
        "time_section": {"as_of": now.isoformat()},
        "data_section": {"facts": [{"symbol": "KOSPI", "value": 6729.46}]},
    }

    assert has_usable_structured_evidence(payload, now=now) is False


def test_structured_evidence_rejects_metadata_only_envelope():
    """provider/base_date/note만 있는 envelope는 실제 evidence가 아니다."""
    now = datetime(2026, 7, 27, 6, tzinfo=UTC)

    assert has_usable_structured_evidence(
        {
            "provider": "kr-stock-skill",
            "base_date": "2026-07-27",
            "note": "generated",
        },
        now=now,
    ) is False
