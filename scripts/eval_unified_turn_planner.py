#!/usr/bin/env python3
"""Unified TurnPlanner fixed-gold evaluator의 thin CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simpleclaw.evaluation.turn_planner_eval import (
    FixtureFormatError,
    evaluate_fixture_replays,
    load_fixtures,
)

_DEFAULT_FIXTURE = (
    Path(__file__).parents[1]
    / "tests"
    / "fixtures"
    / "unified_turn_planner_cases.jsonl"
)


def build_parser() -> argparse.ArgumentParser:
    """지원 옵션과 live opt-in 경계를 정의한다."""
    parser = argparse.ArgumentParser(
        description="Evaluate Unified TurnPlanner fixed-gold cases.",
    )
    parser.add_argument("--fixture", type=Path, default=_DEFAULT_FIXTURE)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--reasoning",
        choices=("off", "low", "medium"),
        default="medium",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly opt in to a configured live planner runner.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """오프라인 replay를 평가하고 deterministic JSON을 출력한다."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.live:
        parser.error(
            "live runner is not configured in BIZ-488; "
            "connect it after the production planner contract lands"
        )
    try:
        fixtures = load_fixtures(args.fixture)
        report = evaluate_fixture_replays(
            fixtures,
            repeat=args.repeat,
            variant=args.reasoning,
            baseline="unified",
        )
    except FixtureFormatError as exc:
        parser.error(str(exc))
    serialized = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.json_output:
        args.json_output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
