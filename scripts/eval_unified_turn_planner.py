#!/usr/bin/env python3
"""Unified TurnPlanner fixed-gold evaluator의 thin CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from simpleclaw.evaluation.turn_planner_eval import (
    FixtureFormatError,
    evaluate_fixture_replays,
    load_fixtures,
)
from simpleclaw.eval.turn_planner import evaluate_capability_fixture_file

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
    parser.add_argument("--fixture", "--fixtures", type=Path, default=_DEFAULT_FIXTURE)
    parser.add_argument("--max-cases", type=int)
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
        if args.max_cases is not None and args.max_cases < 1:
            parser.error("--max-cases must be at least 1")
        first_row = json.loads(
            next(
                line
                for line in args.fixture.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        )
        if isinstance(first_row, dict) and "coverage" in first_row:
            report = evaluate_capability_fixture_file(
                args.fixture,
                max_cases=args.max_cases,
            )
        else:
            fixtures = load_fixtures(args.fixture)
            if args.max_cases is not None:
                fixtures = fixtures[: args.max_cases]
            report = evaluate_fixture_replays(
                fixtures,
                repeat=args.repeat,
                variant=args.reasoning,
                baseline="unified",
            )
    except (FixtureFormatError, OSError, ValueError, StopIteration) as exc:
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
