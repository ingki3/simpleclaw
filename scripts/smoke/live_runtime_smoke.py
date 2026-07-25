#!/usr/bin/env python3
"""Read-only live runtime smoke for SimpleClaw.

기본값은 local live 경로를 점검하지만, Telegram 전송/외부 네트워크/DB mutation은
하지 않는다. 운영자는 배포/재시작 후 JSON 또는 Markdown evidence를 Multica
comment에 붙일 수 있다.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path("/Users/simplist/.simpleclaw/config.yaml")
DEFAULT_DAEMON_DB = Path("/Users/simplist/.simpleclaw-agent/default/daemon.db")
DEFAULT_BOT_LOG = Path("/Users/simplist/.simpleclaw-agent/default/bot.log")


def _expand(value: str | None) -> Path | None:
    """Expand a config path value if present."""
    if not value:
        return None
    return Path(value).expanduser()


def _exists_check(path: Path | None) -> dict[str, Any]:
    """Return a safe existence summary for one path."""
    if path is None:
        return {"path": None, "exists": False, "is_dir": False}
    return {"path": str(path), "exists": path.exists(), "is_dir": path.is_dir()}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML config without treating a missing file as an exception."""
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _cron_summary(db_path: Path) -> dict[str, Any]:
    """Read cron job/failure summary from daemon.db in read-only mode."""
    if not db_path.exists():
        return {"path": str(db_path), "exists": False, "jobs": 0, "recent_failures": []}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "cron_jobs" not in tables:
            return {
                "path": str(db_path),
                "exists": True,
                "jobs": 0,
                "recent_failures": [],
            }
        jobs = conn.execute("SELECT COUNT(*) FROM cron_jobs").fetchone()[0]
        failures: list[dict[str, Any]] = []
        if "cron_executions" in tables:
            for row in conn.execute(
                """
                SELECT job_name, status, started_at, finished_at, error_details
                FROM cron_executions
                WHERE status != 'success'
                ORDER BY started_at DESC
                LIMIT 5
                """
            ):
                failures.append(
                    {
                        "job_name": row[0],
                        "status": row[1],
                        "started_at": row[2],
                        "finished_at": row[3],
                        "error": row[4],
                    }
                )
        return {
            "path": str(db_path),
            "exists": True,
            "jobs": jobs,
            "recent_failures": failures,
        }


def build_smoke(config_path: Path, daemon_db: Path, bot_log: Path) -> dict[str, Any]:
    """Build a no-network, no-send live runtime smoke payload."""
    cfg = _load_yaml(config_path)
    recipes_dir = _expand((cfg.get("recipes") or {}).get("dir"))
    study_wiki_dir = _expand((cfg.get("study") or {}).get("wiki_dir"))
    checks = {
        "config": _exists_check(config_path),
        "recipes_dir": _exists_check(recipes_dir),
        "study_wiki_dir": _exists_check(study_wiki_dir),
        "daemon_db": _cron_summary(daemon_db),
        "bot_log": _exists_check(bot_log),
    }
    required_ok = (
        checks["config"]["exists"]
        and checks["recipes_dir"]["exists"]
        and checks["study_wiki_dir"]["exists"]
    )
    return {
        "ok": bool(required_ok),
        "telegram_send_attempted": False,
        "checks": checks,
    }


def _markdown(payload: dict[str, Any]) -> str:
    """Render smoke payload as compact Markdown evidence."""
    rows = ["| Check | OK | Detail |", "|---|---:|---|"]
    for name, check in payload["checks"].items():
        if name == "daemon_db":
            ok = check["exists"]
            detail = (
                f"jobs={check.get('jobs', 0)}, "
                f"recent_failures={len(check.get('recent_failures', []))}"
            )
        else:
            ok = check["exists"]
            detail = check["path"]
        rows.append(f"| {name} | {ok} | `{detail}` |")
    rows.append("| telegram_send_attempted | False | no user notification sent |")
    return "\n".join(rows) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="SimpleClaw read-only live runtime smoke"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--daemon-db", type=Path, default=DEFAULT_DAEMON_DB)
    parser.add_argument("--bot-log", type=Path, default=DEFAULT_BOT_LOG)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-telegram-send", action="store_true", default=True)
    args = parser.parse_args(argv)

    payload = build_smoke(args.config, args.daemon_db, args.bot_log)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_markdown(payload), end="")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
