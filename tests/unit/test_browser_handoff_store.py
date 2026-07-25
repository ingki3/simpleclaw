"""browser_handoff TTL store tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from simpleclaw.browser_handoff.store import BrowserHandoffStore, normalize_url


def test_store_creates_pending_request(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)

    req = store.create_request("https://example.com/a?utm_source=x&keep=1#frag")

    assert req.request_id
    assert req.status == "pending"
    assert req.normalized_url == "https://example.com/a?keep=1"
    assert store.get_request(req.request_id).url.endswith("#frag")


def test_store_receives_page_by_request_id(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)
    req = store.create_request("https://example.com/a")

    page = store.receive_page(
        {
            "request_id": req.request_id,
            "url": "https://example.com/a",
            "title": "Example",
            "text": "Hello world",
            "has_password_field": False,
        }
    )

    assert page.request_id == req.request_id
    assert page.text == "Hello world"
    assert store.get_request(req.request_id).status == "received"
    assert store.read(req.request_id).title == "Example"


def test_store_matches_latest_pending_by_normalized_url(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)
    req = store.create_request("https://example.com/a#simpleclaw_request=abc")

    page = store.receive_page(
        {
            "url": "https://example.com/a",
            "title": "Example",
            "text": "Matched without request id",
            "has_password_field": False,
        }
    )

    assert page.request_id == req.request_id
    assert page.warning is None


def test_store_expires_old_requests(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)
    req = store.create_request("https://example.com/a")
    path = tmp_path / "requests" / f"{req.request_id}.json"
    data = json.loads(path.read_text())
    data["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")

    assert store.expire_old_requests() == 1
    assert store.get_request(req.request_id).status == "expired"


def test_normalize_url_strips_fragment_and_tracking_params():
    assert normalize_url("https://Example.com/p?utm_campaign=x&b=2&gclid=y#section") == "https://example.com/p?b=2"
