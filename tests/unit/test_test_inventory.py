"""테스트 인벤토리 스크립트 회귀 테스트."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_test_inventory_outputs_expected_keys():
    """인벤토리 JSON은 주요 테스트 계층과 workflow 목록을 포함한다."""
    script = Path("scripts/dev/test_inventory.py")
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["tests"]["unit"]["exists"] is True
    assert payload["tests"]["unit"]["test_files"] >= 1
    assert payload["tests"]["integration"]["exists"] is True
    assert "contracts" in payload["tests"]
    assert ".github/workflows/unit-tests.yml" in payload["workflows"]
