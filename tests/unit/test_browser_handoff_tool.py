"""browser_handoff native tool handler tests."""

from __future__ import annotations

import pytest

from simpleclaw.agent.browser_handoff_tool import handle_browser_handoff
from simpleclaw.browser_handoff.store import BrowserHandoffStore


def _cfg(tmp_path, **overrides):
    cfg = {
        "enabled": True,
        "chrome_app": "Google Chrome",
        "store_dir": str(tmp_path),
        "request_ttl_seconds": 600,
        "open_wait_seconds": 90,
        "max_extracted_chars": 50000,
        "sensitive_domain_policy": "block",
    }
    cfg.update(overrides)
    return cfg


@pytest.mark.asyncio
async def test_browser_handoff_disabled(tmp_path):
    result = await handle_browser_handoff(
        {"action": "open_and_wait", "url": "https://example.com/a"},
        config=_cfg(tmp_path, enabled=False),
        interactive=True,
    )

    assert "disabled" in result.lower()


@pytest.mark.asyncio
async def test_browser_handoff_disabled_for_cron(tmp_path):
    result = await handle_browser_handoff(
        {"action": "open_and_wait", "url": "https://example.com/a"},
        config=_cfg(tmp_path),
        interactive=False,
    )

    assert "interactive" in result.lower()


@pytest.mark.asyncio
async def test_browser_handoff_open_and_wait_times_out(tmp_path):
    opened = []

    async def fake_open(url, chrome_app):
        opened.append((url, chrome_app))

    result = await handle_browser_handoff(
        {"action": "open_and_wait", "url": "https://example.com/a", "wait_seconds": 0},
        config=_cfg(tmp_path),
        interactive=True,
        opener=fake_open,
    )

    assert opened
    assert "simpleclaw_request=" in opened[0][0]
    assert opened[0][1] == "Google Chrome"
    assert "BROWSER_HANDOFF_PENDING" in result
    assert "copy/paste" in result
    assert "본문을 복사" not in result


@pytest.mark.asyncio
async def test_browser_handoff_read_returns_stored_content(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)
    req = store.create_request("https://example.com/a")
    store.receive_page(
        {
            "request_id": req.request_id,
            "url": "https://example.com/a",
            "title": "Example",
            "text": "Extracted body",
        }
    )

    result = await handle_browser_handoff(
        {"action": "read", "request_id": req.request_id},
        config=_cfg(tmp_path),
        interactive=True,
    )

    assert result.startswith("BROWSER_HANDOFF_CONTENT")
    assert "Extracted body" in result


@pytest.mark.asyncio
async def test_browser_handoff_blocks_internal_url(tmp_path):
    result = await handle_browser_handoff(
        {"action": "open_and_wait", "url": "http://127.0.0.1:8082/admin", "wait_seconds": 0},
        config=_cfg(tmp_path),
        interactive=True,
    )

    assert "blocked" in result.lower()


@pytest.mark.asyncio
async def test_browser_handoff_blocks_sensitive_url(tmp_path):
    result = await handle_browser_handoff(
        {"action": "open_and_wait", "url": "https://accounts.google.com/login", "wait_seconds": 0},
        config=_cfg(tmp_path),
        interactive=True,
    )

    assert "sensitive" in result.lower()
