import pytest

from simpleclaw.agent.evidence_retrieval import EvidenceRetriever
from simpleclaw.agent.fact_types import EvidenceCoverage


@pytest.mark.asyncio
async def test_retriever_converts_search_text_to_evidence(monkeypatch):
    async def fake_web_search(args, body_fetcher=None):
        return """
1. Official standings
URL: https://official.example/standings
Snippet: Final standings updated today.
"""

    monkeypatch.setattr(
        "simpleclaw.agent.evidence_retrieval.handle_web_search",
        fake_web_search,
    )

    retriever = EvidenceRetriever(max_sources_per_slot=2)
    evidence = await retriever.search_for_slot("current_state", "official current standings")

    assert evidence
    assert evidence[0].source_url == "https://official.example/standings"
    assert evidence[0].coverage in {EvidenceCoverage.UNKNOWN, EvidenceCoverage.FINAL}


@pytest.mark.asyncio
async def test_retriever_does_not_infer_final_from_query_header(monkeypatch):
    async def fake_web_search(args, body_fetcher=None):
        return """Search results for: 현재 결과 확인
1. Generic page
URL: https://example.com/page
Snippet: No status marker here.
"""

    monkeypatch.setattr(
        "simpleclaw.agent.evidence_retrieval.handle_web_search",
        fake_web_search,
    )

    retriever = EvidenceRetriever(max_sources_per_slot=1)
    evidence = await retriever.search_for_slot("current_state", "현재 결과 확인")

    assert evidence[0].coverage == EvidenceCoverage.UNKNOWN
