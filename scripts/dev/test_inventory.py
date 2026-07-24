#!/usr/bin/env python3
"""SimpleClaw 테스트/CI 인벤토리 요약.

읽기 전용 스크립트다. Review Agent와 운영자가 CI 누락, contract test 존재
여부, runtime smoke 존재 여부를 같은 기준으로 확인할 수 있게 JSON/Markdown을
출력한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _test_dir_summary(relative: str) -> dict[str, Any]:
    """Return a deterministic summary for one pytest directory."""
    path = ROOT / relative
    files = sorted(path.rglob("test_*.py")) if path.exists() else []
    return {
        "path": relative,
        "exists": path.exists(),
        "test_files": len(files),
        "files": [str(p.relative_to(ROOT)) for p in files],
    }


def build_inventory() -> dict[str, Any]:
    """Build a read-only inventory of test and workflow surfaces."""
    workflow_dir = ROOT / ".github" / "workflows"
    workflows = (
        sorted(str(p.relative_to(ROOT)) for p in workflow_dir.glob("*.yml"))
        if workflow_dir.exists()
        else []
    )
    smoke_dir = ROOT / "scripts" / "smoke"
    smoke_files = (
        sorted(str(p.relative_to(ROOT)) for p in smoke_dir.rglob("*.py"))
        if smoke_dir.exists()
        else []
    )
    return {
        "tests": {
            "unit": _test_dir_summary("tests/unit"),
            "integration": _test_dir_summary("tests/integration"),
            "contracts": _test_dir_summary("tests/contracts"),
        },
        "workflows": workflows,
        "runtime_smoke": {
            "path": "scripts/smoke",
            "exists": smoke_dir.exists(),
            "files": smoke_files,
        },
    }


def _markdown(payload: dict[str, Any]) -> str:
    """Render inventory as compact Markdown for PR/Multica comments."""
    rows = ["| Area | Exists | Test files / files |", "|---|---:|---:|"]
    for name, item in payload["tests"].items():
        rows.append(f"| tests/{name} | {item['exists']} | {item['test_files']} |")
    rows.append(
        f"| scripts/smoke | {payload['runtime_smoke']['exists']} | "
        f"{len(payload['runtime_smoke']['files'])} |"
    )
    rows.append(f"| .github/workflows | True | {len(payload['workflows'])} |")
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Summarize SimpleClaw test/CI inventory"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    payload = build_inventory()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
