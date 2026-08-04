#!/usr/bin/env python3
"""LangGraph V4 connected shadow validator thin wrapper."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from simpleclaw.langgraph_v4_shadow_validation import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
