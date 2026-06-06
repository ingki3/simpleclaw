"""Install the SimpleClaw realtime lookup runtime skill.

이 스크립트는 배포 repo의 Python module(`simpleclaw.skills.realtime_lookup`)을
런타임 skill discovery 경로에서 실행할 수 있도록 wrapper SKILL.md와 script를 쓴다.
스킬 자체는 사용자 홈의 `~/.agents/skills`에 설치되며, 코드는 배포된 SimpleClaw
패키지를 import하므로 PR 배포 후 재설치하면 live 코드와 함께 동작한다.
"""

from __future__ import annotations

from pathlib import Path

SKILL_NAME = "realtime-lookup-skill"
DEFAULT_GLOBAL_SKILLS_DIR = Path("~/.agents/skills").expanduser()

SKILL_MD = """---
name: realtime-lookup-skill
description: Produce structured JSON evidence for live/current facts such as news, weather, sports scores, and market data before the LLM writes a final answer.
retry:
  max_retries: 1
  initial_backoff_seconds: 0.5
  backoff_factor: 2.0
  max_backoff_seconds: 2.0
  idempotent: true
  retry_on_timeout: false
---

# realtime-lookup-skill

실시간성 질문(오늘/현재/최신 뉴스, 날씨, 주가/시장, 경기 결과 등)에 대해 최종 답변 전 근거 JSON을 생성합니다.

## When to use

- 현재/최신/오늘/방금 같은 시간 cue가 있는 뉴스·날씨·시장·스포츠 질문
- 과거 답변의 최신 사실 확인/정정 요청
- 크론 보고서처럼 최신 기사/시장 상황을 근거로 써야 하는 작업

## Contract

- 입력: 오케스트레이터가 전달하는 URL-safe base64 JSON 단일 토큰
- 출력: 아래 필드를 갖는 JSON
  - `kind`: `news` / `weather` / `market` / `sports` / `general`
  - `query`
  - `freshness`
  - `confidence`
  - `evidence[]`
  - `facts[]`
  - `limitations[]`

## Script

Target: `realtime_lookup_skill.py`
"""

WRAPPER = """#!/usr/bin/env python3
from simpleclaw.skills.realtime_lookup import main

if __name__ == "__main__":
    raise SystemExit(main())
"""


def install(global_dir: Path = DEFAULT_GLOBAL_SKILLS_DIR) -> Path:
    """runtime global skills directory에 realtime lookup skill wrapper를 설치한다."""
    skill_dir = global_dir / SKILL_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    wrapper = skill_dir / "realtime_lookup_skill.py"
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
