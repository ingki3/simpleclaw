"""Review 서브시스템(config.yaml ``review`` 섹션) 설정 로더 (BIZ-440).

subagent review ledger 의 저장 경로/보존 기간을 로드한다. 다른 config loader 와
같은 원칙 — 파일이 없거나 파싱에 실패해도 항상 안전한 기본값으로 동작한다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from simpleclaw.config_sections.agents import _coerce_int_config

# subagent review ledger 기본값. 경로는 다른 운영 데이터(conversations.db,
# suggestions JSONL 등)와 같은 런타임 루트 아래에 둔다. retention_days 는
# 완료된 record 의 보존 기간 — 미완료(running/late) record 는 기간과 무관하게
# 보존된다 (subagent_ledger._apply_retention 참고).
_REVIEW_DEFAULTS: dict = {
    "subagent_ledger": {
        "path": "~/.simpleclaw-agent/default/review_subagent_ledger.jsonl",
        "retention_days": 90,
    },
}


def load_review_config(config_path: str | Path) -> dict:
    """config.yaml 의 ``review`` 섹션을 기본값으로 보강해 로드한다."""
    config_path = Path(config_path)
    raw: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            review = data.get("review", {}) if isinstance(data, dict) else {}
            raw = review if isinstance(review, dict) else {}
        except (yaml.YAMLError, OSError):
            raw = {}

    ledger_raw = raw.get("subagent_ledger", {})
    if not isinstance(ledger_raw, dict):
        ledger_raw = {}
    defaults = _REVIEW_DEFAULTS["subagent_ledger"]

    path = ledger_raw.get("path", defaults["path"])
    # 빈 문자열 경로는 미설정 사고로 간주하고 기본 경로로 되돌린다.
    if not isinstance(path, str) or not path.strip():
        path = defaults["path"]

    return {
        "subagent_ledger": {
            "path": path.strip(),
            "retention_days": _coerce_int_config(
                ledger_raw.get("retention_days", defaults["retention_days"]),
                defaults["retention_days"],
                minimum=0,
            ),
        },
    }


__all__ = ["_REVIEW_DEFAULTS", "load_review_config"]
