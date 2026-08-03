"""Install the version-controlled Naver Sports runtime skill wrapper.

실제 조회 로직은 배포 package의 :mod:`simpleclaw.skills.naver_sports`가 단일 SoT다.
이 스크립트는 runtime discovery 경로에 문서와 얇은 import wrapper만 설치한다.
"""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "naver-sports-skill"
DEFAULT_GLOBAL_SKILLS_DIR = Path("~/.agents/skills").expanduser()

SKILL_MD = """---
name: naver-sports-skill
description: "Retrieve Naver Sports structured live scores, completed results, and standings with typed event states and source provenance."
---

# Naver Sports Skill

Use the bundled stdlib helper as the numeric source of truth. Preserve returned values
exactly and never fill missing scores, states, ranks, or participants from memory, news,
search results, display text, or broadcast links.

## Registered SimpleClaw invocation

Call the registered skill with `args`; do not put a bare subcommand in `command`.

```text
execute_skill(skill_name="naver-sports-skill", args="--mode live --category kbo --date today --limit 10 --json")
execute_skill(skill_name="naver-sports-skill", args="--mode results --category kbo --date 2026-08-02 --limit 10 --json")
execute_skill(skill_name="naver-sports-skill", args="--mode standings --category epl --limit 10 --json")
```

## Inputs

- `--mode live|results|standings`
  - `live`: current events with provider `STARTED` state only.
  - `results`: completed conventional schedule events with provider `ENDED|RESULT` only.
  - `standings`: team/player rankings for the selected season.
- `--category`: KBO/MLB/NPB, supported football, basketball, volleyball,
  general/tennis, golf, and eSports aliases documented by `--help`.
- `--date today|YYYY-MM-DD`: KST reference date. `results` requires the requested
  result date; `live` immediately refreshes the same endpoint and uses the second result.
- `--season`: optional standings season code/year/title.
- `--limit`: 1 through 20.
- `--json`: compatibility flag; stdout is always one bounded JSON document.

## Output contract

The helper emits one JSON object with `ok`, `source`, `mode`, `category`, `season`,
`date`, `fetched_at`, `freshness`, `items`, `warnings`, and `error`.

- Every result item preserves `event_state`, `status_code`, score, winner, date,
  `fetched_at`, and `source_url`.
- Cancelled, suspended, postponed, unknown, or score-incomplete events are never
  promoted to final results; `results` preserves their typed state in `excluded_events`.
- Normal empty is `ok=true`, `items=[]`, plus an explicit `message`.
- Upstream/input/schema failure is `ok=false` with stable `error.code`,
  `error.message`, and `error.retryable`.
- Naver data can lag the venue. State this limitation when answering.

## Direct diagnostics

```bash
python3 scripts/naver_sports.py --mode live --category kbo --date today --json
python3 scripts/naver_sports.py --mode results --category kbo --date 2026-08-02 --json
python3 scripts/naver_sports.py --mode standings --category pga --limit 10 --json
```
"""

WRAPPER = """#!/usr/bin/env python3
from simpleclaw.skills.naver_sports import main

if __name__ == "__main__":
    raise SystemExit(main())
"""


def install(global_dir: Path = DEFAULT_GLOBAL_SKILLS_DIR) -> Path:
    """runtime global skill 경로에 문서와 import wrapper를 설치한다."""
    skill_dir = global_dir / SKILL_NAME
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    wrapper = scripts_dir / "naver_sports.py"
    wrapper.write_text(WRAPPER, encoding="utf-8")
    wrapper.chmod(0o755)
    return skill_dir


def main() -> int:
    """CLI entrypoint."""
    path = install()
    print(f"installed {SKILL_NAME} at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
