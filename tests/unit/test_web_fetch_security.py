"""BIZ-443 — web_fetch SSRF/cloud metadata 차단 정책 회귀 테스트.

``_is_blocked_url`` 의 차단 범위(클라우드 메타데이터 IP·호스트명, 사설망,
link-local, CGNAT, IPv6, 내부 TLD)와, 정적 fetch 의 redirect hop 검사,
web_search 본문 보강 경로의 동일 정책 적용을 고정한다.
"""

import sys
from types import SimpleNamespace

import pytest

from simpleclaw.agent.builtin_tools import (
    _BLOCKED_URL_MESSAGE,
    _fetch_search_result_body,
    _fetch_static,
    _is_blocked_url,
    handle_web_fetch,
    resolve_web_page_link,
)


class TestBlockedUrls:
    @pytest.mark.parametrize(
        "url",
        [
            # 클라우드 메타데이터 endpoint (AWS/GCP/Azure/Alibaba)
            "http://169.254.169.254/latest/meta-data/",
            "http://169.254.169.254/latest/api/token",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://metadata.goog/computeMetadata/v1/",
            "http://metadata/latest/",
            "http://100.100.100.200/latest/meta-data/",
            # loopback / unspecified
            "http://localhost:8082/admin",
            "http://127.0.0.1:8082/api/status",
            "http://127.1.2.3/",
            "http://0.0.0.0:8080/",
            "http://[::1]:8080/",
            # 사설망 / link-local / CGNAT
            "http://10.0.0.5/",
            "http://192.168.0.10/router",
            "http://172.16.0.1/",
            "http://172.31.255.255/",
            "http://169.254.0.1/",
            "http://100.64.0.1/",
            "http://[fe80::1]/",
            "http://[fd00::1]/",
            # 내부 전용 호스트명
            "http://intranet.local/",
            "http://vault.internal/secrets",
            "http://app.localhost/",
            # http(s) 외 스킴
            "file:///etc/passwd",
            "gopher://example.com/",
            "ftp://example.com/",
        ],
    )
    def test_blocked(self, url):
        assert _is_blocked_url(url) is True, url

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/page",
            "https://www.wikidocs.net/12345",
            "http://93.184.216.34/",  # 공인 IP 리터럴
            "https://sub.domain.co.kr/path?q=1",
        ],
    )
    def test_allowed(self, url):
        assert _is_blocked_url(url) is False, url

    @pytest.mark.asyncio
    async def test_handle_web_fetch_blocks_metadata_url(self):
        result = await handle_web_fetch(
            {"url": "http://169.254.169.254/latest/meta-data/"}
        )
        assert result == _BLOCKED_URL_MESSAGE

    @pytest.mark.asyncio
    async def test_search_body_enrichment_skips_blocked_url(self):
        assert await _fetch_search_result_body("http://metadata.google.internal/") == ""
        assert await _fetch_search_result_body("http://10.0.0.8/data") == ""


class _FakeResponse:
    def __init__(self, status, *, headers=None, body="", reason="OK"):
        self.status = status
        self.headers = headers or {}
        self.reason = reason
        self.content_type = "text/html"
        self._body = body
        self.released = False

    def release(self):
        self.released = True

    async def text(self, errors="replace"):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _install_fake_aiohttp(monkeypatch, responses):
    """`import aiohttp`가 순차 응답을 돌려주는 fake 모듈을 보게 한다."""
    calls: list[str] = []

    class FakeSession:
        def __init__(self, timeout=None):
            self._iter = iter(responses)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, allow_redirects=False):
            calls.append(url)
            return next(self._iter)

    fake = SimpleNamespace(
        ClientTimeout=lambda total=None: None,
        ClientSession=FakeSession,
        ClientError=Exception,
    )
    monkeypatch.setitem(sys.modules, "aiohttp", fake)
    return calls


class TestRedirectHopGuard:
    @pytest.mark.asyncio
    async def test_redirect_to_metadata_endpoint_is_blocked(self, monkeypatch):
        """공개 URL이 메타데이터 IP로 redirect 하면 요청 없이 차단한다."""
        calls = _install_fake_aiohttp(
            monkeypatch,
            [
                _FakeResponse(
                    302,
                    headers={"Location": "http://169.254.169.254/latest/api/token"},
                ),
            ],
        )

        result = await _fetch_static("https://example.com/innocent")

        assert result == _BLOCKED_URL_MESSAGE
        # 차단된 목적지로는 GET 이 나가지 않아야 한다.
        assert calls == ["https://example.com/innocent"]

    @pytest.mark.asyncio
    async def test_relative_redirect_to_public_url_is_followed(self, monkeypatch):
        calls = _install_fake_aiohttp(
            monkeypatch,
            [
                _FakeResponse(301, headers={"Location": "/moved"}),
                _FakeResponse(200, body="<html><body>" + "content " * 100 + "</body></html>"),
            ],
        )

        result = await _fetch_static("https://example.com/old")

        assert not result.startswith("Error:")
        assert "content" in result
        assert calls == ["https://example.com/old", "https://example.com/moved"]

    @pytest.mark.asyncio
    async def test_too_many_redirects_returns_error(self, monkeypatch):
        hops = [
            _FakeResponse(302, headers={"Location": f"https://example.com/{i}"})
            for i in range(10)
        ]
        _install_fake_aiohttp(monkeypatch, hops)

        result = await _fetch_static("https://example.com/loop")

        assert result.startswith("Error: too many redirects")


class TestSafePageLinkResolution:
    @pytest.mark.asyncio
    async def test_resolves_matching_same_site_article_from_publisher_home(self, monkeypatch):
        home = "https://publisher.example/"
        title = "한국형 AI 에이전트 키운다 KAIST 공동연구소 설립"
        body = f"""<html><body>
          <a href="/ads">무관한 광고</a>
          <a href="/news/123">{title}</a>
        </body></html>"""
        calls = _install_fake_aiohttp(monkeypatch, [_FakeResponse(200, body=body)])

        result = await resolve_web_page_link(home, title)

        assert result == "https://publisher.example/news/123"
        assert calls == [home]

    @pytest.mark.asyncio
    async def test_rejects_matching_external_or_private_article_link(self, monkeypatch):
        home = "https://publisher.example/"
        title = "검증 기사 제목"
        body = f"""<html><body>
          <a href="https://evil.example/{title}">{title}</a>
          <a href="http://169.254.169.254/latest">{title}</a>
        </body></html>"""
        _install_fake_aiohttp(monkeypatch, [_FakeResponse(200, body=body)])

        assert await resolve_web_page_link(home, title) is None
