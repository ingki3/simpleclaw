"""Install the Chrome Native Messaging manifest for SimpleClaw browser handoff."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

HOST_NAME = "com.simpleclaw.browser_handoff"


def build_manifest(*, host_name: str, host_path: str, extension_id: str) -> dict:
    """Chrome Native Messaging manifest dict를 생성한다."""

    return {
        "name": host_name,
        "description": "SimpleClaw Browser Handoff Native Host",
        "path": host_path,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def _validate_extension_id(extension_id: str) -> None:
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        raise ValueError("extension id must be 32 lowercase chars in the range a-p")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install SimpleClaw Chrome Native Messaging host")
    parser.add_argument("--extension-id", required=True)
    parser.add_argument("--config", default="/Users/simplist/.simpleclaw/config.yaml")
    parser.add_argument("--python", default="/Users/simplist/.simpleclaw/.venv/bin/python")
    parser.add_argument("--runtime-dir", default="~/.simpleclaw-agent/default/browser-handoff")
    args = parser.parse_args(argv)

    _validate_extension_id(args.extension_id)
    runtime_dir = Path(args.runtime_dir).expanduser()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    wrapper = runtime_dir / "native_host.sh"
    wrapper.write_text(
        "#!/bin/sh
"
        "export HOME=/Users/simplist
"
        f"export SIMPLECLAW_CONFIG={args.config}
"
        f"exec {args.python} -m simpleclaw.browser_handoff.native_host
",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    manifest_dir = Path.home() / "Library/Application Support/Google/Chrome/NativeMessagingHosts"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{HOST_NAME}.json"
    manifest = build_manifest(
        host_name=HOST_NAME,
        host_path=str(wrapper),
        extension_id=args.extension_id,
    )
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"installed {manifest_path}")
    print(f"wrapper {wrapper}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
