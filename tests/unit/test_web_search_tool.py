"""BIZ-365 — query 기반 web_search 내장 도구 테스트.

검색 backend는 네트워크를 직접 호출하지 않고 mock하여 handler의 입력 검증,
결과 compact 렌더링, limit clamp, 실패 메시지를 검증한다.
"""

from __future__ import annotations

import pytest

from simpleclaw.agent.builtin_tools import (
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
