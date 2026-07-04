"""BIZ-365 — query 기반 web_search 내장 도구 테스트.

검색 backend는 네트워크를 직접 호출하지 않고 mock하여 handler의 입력 검증,
결과 compact 렌더링, limit clamp, 실패 메시지를 검증한다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent import builtin_tools
from simpleclaw.agent.builtin_tools import (
    _fetch_search_result_body,
    _parse_duckduckgo_html,
    handle_web_search,
)


@pytest.mark.asyncio
async def test_handle_web_search_renders_compact_results():
    """backend 결과는 title/url/snippet/source 중심 compact text로 반환되어야 한다."""
    calls: list[tuple[str, int]] = []

    async def backend(query: str, limit: int) -> list[dict[str, str]]:
        calls.append((query, limit))
        return [
            {
                "title": "Acme earnings beat expectations",
                "url": "https://news.example.com/acme-earnings",
                "snippet": "Acme reported strong quarterly growth.",
                "source": "mock-search",
            }
        ]

    result = await handle_web_search(
        {"query": "Acme earnings news", "limit": 3},
        search_backend=backend,
    )

    assert calls == [("Acme earnings news", 3)]
    assert "WEB_SEARCH_RESULTS" in result
    assert "Acme earnings beat expectations" in result
    assert "URL: https://news.example.com/acme-earnings" in result
    assert "Snippet: Acme reported strong quarterly growth." in result
    assert "Source: mock-search" in result
    assert "Call web_fetch" in result


@pytest.mark.asyncio
async def test_handle_web_search_clamps_limit_to_ten():
    """LLM이 과도한 limit을 보내도 backend 호출은 10개로 제한된다."""
    seen_limit = None

    async def backend(query: str, limit: int) -> list[dict[str, str]]:
        nonlocal seen_limit
        seen_limit = limit
        return []

    await handle_web_search(
        {"query": "market news", "limit": 999},
        search_backend=backend,
    )

    assert seen_limit == 10


@pytest.mark.asyncio
async def test_handle_web_search_backend_failure_is_llm_readable():
    """검색 backend 예외는 LLM-readable 오류와 fallback 안내로 변환된다."""

    async def backend(query: str, limit: int) -> list[dict[str, str]]:
        raise RuntimeError("network unavailable")

    result = await handle_web_search(
        {"query": "latest semiconductor news"},
        search_backend=backend,
    )

    assert result.startswith("Error: web_search failed")
    assert "network unavailable" in result
    assert "web_fetch" in result


@pytest.mark.asyncio
async def test_handle_web_search_requires_query():
    """query가 없으면 backend 호출 없이 required 오류를 반환한다."""
    result = await handle_web_search({"limit": 5})

    assert "query" in result
    assert "required" in result


@pytest.mark.asyncio
async def test_handle_web_search_enriches_top_results_with_body():
    """body_fetcher가 주어지면 상위 결과에 본문 발췌가 동봉된다."""

    async def backend(query: str, limit: int) -> list[dict[str, str]]:
        return [
            {"title": "T1", "url": "https://a.com/1", "snippet": "s1", "source": "m"},
            {"title": "T2", "url": "https://b.com/2", "snippet": "s2", "source": "m"},
            {"title": "T3", "url": "https://c.com/3", "snippet": "s3", "source": "m"},
        ]

    fetched: list[str] = []

    async def body_fetcher(url: str) -> str:
        fetched.append(url)
        return f"BODY of {url} with the real number 0.286"

    result = await handle_web_search(
        {"query": "kbo stats", "limit": 5},
        search_backend=backend,
        body_fetcher=body_fetcher,
        enrich_count=2,
    )

    # 상위 2개만 본문 회수 → 지연/토큰 제한.
    assert fetched == ["https://a.com/1", "https://b.com/2"]
    assert "Body: BODY of https://a.com/1 with the real number 0.286" in result
    assert "Body: BODY of https://b.com/2" in result
    # 3번째 결과는 본문 없이 snippet만.
    assert "Body: BODY of https://c.com/3" not in result
    assert "prefer it for facts/numbers" in result


@pytest.mark.asyncio
async def test_handle_web_search_without_body_fetcher_is_snippet_only():
    """body_fetcher가 없으면 기존 snippet-only 동작과 안내문을 유지한다."""

    async def backend(query: str, limit: int) -> list[dict[str, str]]:
        return [{"title": "T", "url": "https://a.com", "snippet": "s", "source": "m"}]

    result = await handle_web_search({"query": "q"}, search_backend=backend)

    assert "Body:" not in result
    assert "Detailed page content is not included" in result


@pytest.mark.asyncio
async def test_handle_web_search_body_fetch_failure_degrades_gracefully():
    """개별 본문 회수가 실패/빈 문자열이어도 snippet 결과는 그대로 반환된다."""

    async def backend(query: str, limit: int) -> list[dict[str, str]]:
        return [{"title": "T", "url": "https://a.com", "snippet": "keep me", "source": "m"}]

    async def body_fetcher(url: str) -> str:
        raise RuntimeError("boom")

    result = await handle_web_search(
        {"query": "q"},
        search_backend=backend,
        body_fetcher=body_fetcher,
    )

    assert "Snippet: keep me" in result
    assert "Body:" not in result


@pytest.mark.asyncio
async def test_fetch_search_result_body_skips_block_and_error(monkeypatch):
    """차단 페이지/오류 응답은 빈 문자열로 건너뛰고, 정상 본문만 길이 제한해 반환한다."""
    # 차단 페이지(짧은 본문) → 빈 문자열
    monkeypatch.setattr(builtin_tools, "_fetch_static", AsyncMock(return_value="짧음"))
    assert await _fetch_search_result_body("https://x.com") == ""

    # Error: 프리픽스 → 빈 문자열
    monkeypatch.setattr(
        builtin_tools, "_fetch_static", AsyncMock(return_value="Error: HTTP 500")
    )
    assert await _fetch_search_result_body("https://x.com") == ""

    # 내부/로컬 URL → fetch 없이 빈 문자열
    assert await _fetch_search_result_body("http://127.0.0.1/admin") == ""

    # 정상 본문 → 길이 제한 적용
    long_body = "팩트 " * 2000
    monkeypatch.setattr(
        builtin_tools, "_fetch_static", AsyncMock(return_value=long_body)
    )
    out = await _fetch_search_result_body("https://x.com")
    assert out.endswith("…[truncated]")
    assert len(out) <= builtin_tools._WEB_SEARCH_BODY_MAX + len(" …[truncated]")


def test_parse_duckduckgo_html_decodes_title_url_and_snippet():
    """DuckDuckGo HTML redirect URL의 uddg를 실제 URL로 복원한다."""
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle&amp;rut=abc">
        Example &amp; Article
      </a>
      <a class="result__snippet">A <b>short</b> snippet.</a>
    </div>
    """

    results = _parse_duckduckgo_html(html, limit=5)

    assert results == [
        {
            "title": "Example & Article",
            "url": "https://example.com/article",
            "snippet": "A short snippet.",
            "source": "duckduckgo-html",
        }
    ]
