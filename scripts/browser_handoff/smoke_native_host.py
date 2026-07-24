"""Smoke-test the browser handoff native host without Chrome."""

from __future__ import annotations

import json
import subprocess
import sys

from simpleclaw.browser_handoff.native_host import (
    decode_native_message,
    encode_native_message,
)


def main() -> int:
    payload = {
        "type": "page_text",
        "url": "https://example.com/article",
        "title": "Example Article",
        "text": "Example visible article text from Chrome extension.",
        "has_password_field": False,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "simpleclaw.browser_handoff.native_host"],
        input=encode_native_message(payload),
        capture_output=True,
        check=False,
    )
    response = decode_native_message(proc.stdout)
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
