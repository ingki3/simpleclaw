"""Chrome Native Messaging host tests for browser_handoff."""

from __future__ import annotations

import json
import struct

from simpleclaw.browser_handoff.native_host import (
    decode_native_message,
    encode_native_message,
    handle_message,
)
from simpleclaw.browser_handoff.store import BrowserHandoffStore


def test_native_host_decodes_length_prefixed_message():
    payload = json.dumps({"type": "ping"}).encode()
    raw = struct.pack("<I", len(payload)) + payload

    assert decode_native_message(raw) == {"type": "ping"}


def test_native_host_encodes_length_prefixed_message():
    raw = encode_native_message({"ok": True})
    size = struct.unpack("<I", raw[:4])[0]

    assert size == len(raw[4:])
    assert json.loads(raw[4:].decode()) == {"ok": True}


def test_native_host_rejects_password_page(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)

    response = handle_message(
        {
            "type": "page_text",
            "url": "https://example.com/login",
            "title": "Login",
            "text": "Secret page",
            "has_password_field": True,
        },
        store=store,
        max_chars=50000,
    )

    assert response["ok"] is False
    assert "password" in response["error"].lower()
    assert store.read_latest() is None


def test_native_host_rejects_internal_url(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)

    response = handle_message(
        {
            "type": "page_text",
            "url": "http://127.0.0.1:8082/admin",
            "title": "Admin",
            "text": "Admin page",
            "has_password_field": False,
        },
        store=store,
        max_chars=50000,
    )

    assert response["ok"] is False
    assert "internal" in response["error"].lower()


def test_native_host_stores_valid_page(tmp_path):
    store = BrowserHandoffStore(tmp_path, ttl_seconds=600)
    req = store.create_request("https://example.com/article")

    response = handle_message(
        {
            "type": "page_text",
            "request_id": req.request_id,
            "url": "https://example.com/article",
            "title": "Article",
            "text": "Article text",
            "has_password_field": False,
        },
        store=store,
        max_chars=50000,
    )

    assert response["ok"] is True
    assert response["request_id"] == req.request_id
    assert store.read(req.request_id).text == "Article text"
