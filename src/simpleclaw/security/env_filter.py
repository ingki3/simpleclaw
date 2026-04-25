"""Subprocess environment variable filtering.

Strips sensitive keys (API keys, tokens, secrets) from the environment
before passing it to subprocess calls, preventing accidental exposure.
"""

from __future__ import annotations

import fnmatch
import os

# Glob patterns for keys that should be removed from subprocess environments.
DEFAULT_BLOCKLIST: list[str] = [
    "*_API_KEY",
    "*_TOKEN",
    "*_SECRET",
    "*_PASSWORD",
    "TELEGRAM_*",
    "OPENAI_*",
    "ANTHROPIC_*",
    "GOOGLE_*",
    "AWS_*",
    "WEBHOOK_*",
    "GH_TOKEN",
    "GITHUB_*",
]


def filter_env(
    passthrough: list[str] | None = None,
    blocklist: list[str] | None = None,
) -> dict[str, str]:
    """Return a copy of ``os.environ`` with sensitive keys removed.

    Args:
        passthrough: Keys to keep even if they match the blocklist.
        blocklist: Override the default blocklist patterns.

    Returns:
        Filtered environment dict safe for subprocess use.
    """
    patterns = blocklist if blocklist is not None else DEFAULT_BLOCKLIST
    keep = set(passthrough or [])
    env = dict(os.environ)

    to_remove = []
    for key in env:
        if key in keep:
            continue
        for pattern in patterns:
            if fnmatch.fnmatch(key, pattern):
                to_remove.append(key)
                break

    for key in to_remove:
        del env[key]

    return env
