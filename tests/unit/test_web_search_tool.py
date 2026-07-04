"""BIZ-365 — query 기반 web_search 내장 도구 테스트.

검색 backend는 네트워크를 직접 호출하지 않고 mock하여 handler의 입력 검증,
결과 compact 렌더링, limit clamp, 실패 메시지를 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.agent import builtin_tools
from simpleclaw.agent.builtin_tools import (
    _fetch_search_result_body,
    _grounding_chunk_entries,
    _parse_duckduckgo_html,
    _web_search_google,
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


# ---------------------------------------------------------------------------
# BIZ-418 — 기본 backend 를 Google Search 그라운딩으로 교체.
# ---------------------------------------------------------------------------


def _make_grounding_response(text: str, chunks: list[tuple[str, str]]):
    """title/uri 튜플 목록으로 Gemini 그라운딩 응답을 흉내내는 fake 객체를 만든다."""

    web_chunks = [
        SimpleNamespace(web=SimpleNamespace(title=title, uri=uri))
        for title, uri in chunks
    ]
    metadata = SimpleNamespace(grounding_chunks=web_chunks)
    candidate = SimpleNamespace(grounding_metadata=metadata)
    return SimpleNamespace(text=text, candidates=[candidate])


def test_grounding_chunk_entries_maps_title_url_source():
    """grounding web 청크는 title/url/source 엔트리로 변환되고 중복 URL 은 제거된다."""
    response = _make_grounding_response(
        "요약",
        [
            ("Acme Q3 earnings", "https://news.example.com/acme"),
            ("Dup", "https://news.example.com/acme"),  # 중복 URL → 제거
            ("Beta report", "https://beta.example.com/report"),
        ],
    )

    entries = _grounding_chunk_entries(response, limit=10)

    assert entries == [
        {
            "title": "Acme Q3 earnings",
            "url": "https://news.example.com/acme",
            "snippet": "",
            "source": "google-grounding",
        },
        {
            "title": "Beta report",
            "url": "https://beta.example.com/report",
            "snippet": "",
            "source": "google-grounding",
        },
    ]


def test_grounding_chunk_entries_without_metadata_is_empty():
    """grounding_metadata 가 없으면 빈 목록을 반환한다(무결과 graceful)."""
    response = SimpleNamespace(text="", candidates=[SimpleNamespace(grounding_metadata=None)])
    assert _grounding_chunk_entries(response, limit=5) == []


@pytest.mark.asyncio
async def test_web_search_google_backend_converts_grounding(monkeypatch):
    """네트워크 없이(monkeypatch) 그라운딩 응답이 요약 + 출처 엔트리로 변환된다."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = _make_grounding_response(
        "Acme beat estimates this quarter.",
        [("Acme Q3", "https://news.example.com/acme")],
    )
    generate = AsyncMock(return_value=response)
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(builtin_tools, "_build_genai_client", lambda api_key: fake_client)

    out = await _web_search_google("Acme earnings", limit=5)

    # 사용한 API key 는 GOOGLE_API_KEY 우선.
    assert generate.await_count == 1
    assert out["summary"] == "Acme beat estimates this quarter."
    assert out["results"] == [
        {
            "title": "Acme Q3",
            "url": "https://news.example.com/acme",
            "snippet": "",
            "source": "google-grounding",
        }
    ]


@pytest.mark.asyncio
async def test_handle_web_search_default_uses_google_grounding(monkeypatch):
    """search_backend 미지정 시 기본 backend 는 Google 그라운딩 경로를 사용한다."""
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    response = _make_grounding_response(
        "Semiconductors rallied on strong demand.",
        [("Chip rally", "https://market.example.com/chips")],
    )
    generate = AsyncMock(return_value=response)
    fake_client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(builtin_tools, "_build_genai_client", lambda api_key: fake_client)

    result = await handle_web_search({"query": "semiconductor news"})

    assert "WEB_SEARCH_RESULTS" in result
    assert "Grounded summary: Semiconductors rallied on strong demand." in result
    assert "URL: https://market.example.com/chips" in result
    assert "Source: google-grounding" in result


@pytest.mark.asyncio
async def test_handle_web_search_missing_api_key_is_readable(monkeypatch):
    """GOOGLE/GEMINI API key 가 모두 없으면 LLM-readable 오류를 반환한다(네트워크 없음)."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(builtin_tools, "_candidate_google_search_config_paths", lambda: [])

    # 키 부재 시 client 생성 이전에 실패해야 하므로, client 생성이 호출되면 실패로 간주.
    def _should_not_build(api_key):
        raise AssertionError("client should not be built without an API key")

    monkeypatch.setattr(builtin_tools, "_build_genai_client", _should_not_build)

    result = await handle_web_search({"query": "latest news"})

    assert result.startswith("Error: web_search failed")
    assert "GOOGLE_API_KEY" in result
    assert "GEMINI_API_KEY" in result


@pytest.mark.asyncio
async def test_web_search_google_falls_back_to_gemini_api_key(monkeypatch):
    """GOOGLE_API_KEY 가 없으면 GEMINI_API_KEY 로 폴백한다."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fallback-key")

    captured: dict[str, str] = {}

    def _build(api_key: str):
        captured["api_key"] = api_key
        response = _make_grounding_response("ok", [("T", "https://x.example.com")])
        return SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=AsyncMock(return_value=response))
            )
        )

    monkeypatch.setattr(builtin_tools, "_build_genai_client", _build)

    out = await _web_search_google("q", limit=3)

    assert captured["api_key"] == "fallback-key"
    assert out["results"][0]["url"] == "https://x.example.com"


@pytest.mark.asyncio
async def test_web_search_google_falls_back_to_config_gemini_key(monkeypatch, tmp_path):
    """환경변수 키가 없으면 config.yaml의 gemini provider api_key를 사용한다."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n"
        "  providers:\n"
        "    gemini:\n"
        "      model: gemini-test-model\n"
        "      api_key: plain:config-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        builtin_tools,
        "_candidate_google_search_config_paths",
        lambda: [config_path],
    )

    captured: dict[str, str] = {}

    def _build(api_key: str):
        captured["api_key"] = api_key
        response = _make_grounding_response("ok", [("T", "https://x.example.com")])
        return SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=AsyncMock(return_value=response))
            )
        )

    monkeypatch.setattr(builtin_tools, "_build_genai_client", _build)

    out = await _web_search_google("q", limit=3)

    assert captured["api_key"] == "config-key"
    assert out["results"][0]["url"] == "https://x.example.com"


@pytest.mark.asyncio
async def test_handle_web_search_custom_backend_overrides_default(monkeypatch):
    """커스텀 search_backend 를 주면 Google 그라운딩 기본 경로를 타지 않는다."""
    # 기본 backend 가 호출되면 즉시 실패시켜, 커스텀 backend 만 사용됨을 보장.
    async def _boom_default(query: str, limit: int):
        raise AssertionError("default google backend must not be called")

    monkeypatch.setattr(builtin_tools, "_web_search_google", _boom_default)

    async def custom_backend(query: str, limit: int) -> list[dict[str, str]]:
        return [
            {"title": "Custom", "url": "https://custom.example.com", "snippet": "s", "source": "custom"}
        ]

    result = await handle_web_search({"query": "q"}, search_backend=custom_backend)

    assert "Custom" in result
    assert "URL: https://custom.example.com" in result
    assert "Source: custom" in result
    assert "Grounded summary:" not in result  # 커스텀 backend 는 요약을 제공하지 않음.


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
