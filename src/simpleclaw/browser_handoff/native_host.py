"""Chrome Native Messaging host for SimpleClaw browser handoff.

Chrome 확장 프로그램은 현재 탭 텍스트를 4-byte little-endian length-prefixed JSON으로
보낸다. 이 host는 payload를 검증한 뒤 TTL store에 저장하고, 결과 JSON을 같은
프로토콜로 반환한다.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import struct
import sys
from pathlib import Path
from urllib.parse import urlparse

from simpleclaw.browser_handoff.store import BrowserHandoffStore
from simpleclaw.config import load_agent_config

_DEFAULT_CONFIG_PATH = "/Users/simplist/.simpleclaw/config.yaml"


def decode_native_message(raw: bytes) -> dict:
    """길이 prefix가 붙은 Native Messaging raw bytes를 dict로 디코드한다."""

    if len(raw) < 4:
        raise ValueError("native message is missing length prefix")
    size = struct.unpack("<I", raw[:4])[0]
    payload = raw[4:]
    if len(payload) != size:
        raise ValueError(f"native message length mismatch: expected {size}, got {len(payload)}")
    return json.loads(payload.decode("utf-8"))


def encode_native_message(payload: dict) -> bytes:
    """dict payload를 Chrome Native Messaging 응답 bytes로 인코드한다."""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("<I", len(body)) + body


def read_native_message(stdin) -> dict:
    """stdin buffer에서 Native Messaging 메시지 1건을 읽는다."""

    header = stdin.read(4)
    if len(header) != 4:
        raise ValueError("native message header missing")
    size = struct.unpack("<I", header)[0]
    body = stdin.read(size)
    if len(body) != size:
        raise ValueError(f"native message body truncated: expected {size}, got {len(body)}")
    return json.loads(body.decode("utf-8"))


def _is_internal_url(url: str) -> bool:
    """내부망/localhost URL이면 True를 반환한다."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def handle_message(
    message: dict,
    *,
    store: BrowserHandoffStore,
    max_chars: int = 50_000,
) -> dict:
    """확장 프로그램 메시지 1건을 검증/저장하고 응답 dict를 반환한다."""

    if message.get("type") != "page_text":
        return {"ok": False, "error": "unsupported message type"}

    url = str(message.get("url") or "").strip()
    text = str(message.get("text") or "").strip()
    if not url:
        return {"ok": False, "error": "url is required"}
    if _is_internal_url(url):
        return {"ok": False, "error": "internal/local URLs are blocked"}
    if bool(message.get("has_password_field")):
        return {"ok": False, "error": "pages with password fields are blocked"}
    if not text:
        return {"ok": False, "error": "empty page text"}

    capped = dict(message)
    capped["text"] = text[:max_chars]
    page = store.receive_page(capped)
    return {
        "ok": True,
        "request_id": page.request_id,
        "status": "received",
        "chars": len(page.text),
        "warning": page.warning,
    }


def _load_store_from_config(config_path: str | Path) -> tuple[BrowserHandoffStore, int]:
    agent = load_agent_config(config_path)
    cfg = agent.get("browser_handoff", {}) or {}
    max_chars = int(cfg.get("max_extracted_chars", 50_000))
    store = BrowserHandoffStore(
        cfg.get("store_dir", "~/.simpleclaw-agent/default/browser-handoff"),
        ttl_seconds=int(cfg.get("request_ttl_seconds", 600)),
        max_chars=max_chars,
    )
    return store, max_chars


def main(argv: list[str] | None = None) -> int:
    """Native Messaging host CLI entrypoint."""

    parser = argparse.ArgumentParser(description="SimpleClaw browser handoff native host")
    parser.add_argument("--config", default=os.environ.get("SIMPLECLAW_CONFIG", _DEFAULT_CONFIG_PATH))
    args = parser.parse_args(argv)

    try:
        store, max_chars = _load_store_from_config(args.config)
        message = read_native_message(sys.stdin.buffer)
        response = handle_message(message, store=store, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001 — native host must always return structured JSON
        response = {"ok": False, "error": str(exc)[:500]}
    sys.stdout.buffer.write(encode_native_message(response))
    sys.stdout.buffer.flush()
    return 0 if response.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
