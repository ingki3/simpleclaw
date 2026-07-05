"""live runtime smoke 스크립트의 안전 동작을 검증한다."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_live_runtime_smoke_help_runs():
    """Smoke CLI는 Telegram 전송 방지 옵션을 문서화한다."""
    result = subprocess.run(
        [sys.executable, "scripts/smoke/live_runtime_smoke.py", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "--no-telegram-send" in result.stdout


def test_live_runtime_smoke_json_with_temp_paths(tmp_path: Path):
    """임시 config/paths만으로 JSON smoke가 성공해야 한다."""
    config = tmp_path / "config.yaml"
    recipes = tmp_path / "recipes"
    wiki = tmp_path / "agent_wiki"
    recipes.mkdir()
    wiki.mkdir()
    config.write_text(
        f"""
recipes:
  dir: {recipes}
study:
  wiki_dir: {wiki}
""".lstrip(),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/smoke/live_runtime_smoke.py",
            "--config",
            str(config),
            "--json",
            "--no-telegram-send",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["checks"]["config"]["exists"] is True
    assert payload["checks"]["recipes_dir"]["exists"] is True
    assert payload["checks"]["study_wiki_dir"]["exists"] is True
    assert payload["telegram_send_attempted"] is False
