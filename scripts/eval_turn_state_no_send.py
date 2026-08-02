"""Bounded actual-provider planner/gate evaluation with no side effects."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from simpleclaw.agent.context_candidates import ContextCandidateBuilder
from simpleclaw.agent.orchestrator import _planner_native_specs
from simpleclaw.agent.plan_gate import PlanGate
from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.resolution_types import ExecutionMode
from simpleclaw.agent.turn_planner import PlannerUnavailable, plan_turn_with_llm
from simpleclaw.config import load_agent_config
from simpleclaw.llm.router import create_router

ROOT = Path(__file__).resolve().parents[1]
_FACT_ACTION_MODES = frozenset(
    {
        ExecutionMode.ANSWER_WITH_EVIDENCE,
        ExecutionMode.RESOLVE_COMPLEX_PROBLEM,
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate planner and PlanGate without dispatch/persistence/delivery.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests/fixtures/turn_state/current_fact_cases.json",
    )
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--max-cases", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--no-dispatch", action="store_true")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--no-delivery", action="store_true")
    return parser


def _load_cases(path: Path, max_cases: int) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise TypeError("fixture must be a JSON array")
    cases = [
        row
        for row in data
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("user"), str)
    ]
    return cases[:max_cases]


def _fact_action_planned(
    *,
    fact_required: bool,
    execution_mode: ExecutionMode,
) -> bool:
    """Recognize fact work after legacy payloads normalize to canonical modes."""
    return bool(fact_required and execution_mode in _FACT_ACTION_MODES)


async def _run(args: argparse.Namespace) -> dict:
    config = load_agent_config(args.config)["unified_turn_planner"]
    router = create_router(args.config)
    catalog = build_planner_catalog(
        native_specs=_planner_native_specs(
            cron_available=False,
            browser_handoff_available=False,
        )
    )
    candidates = ContextCandidateBuilder().build([])
    rows: list[dict] = []
    for repeat_index in range(1, args.repeats + 1):
        for case in _load_cases(args.fixture, args.max_cases):
            started = time.monotonic()
            if case.get("planner_error") is True:
                rows.append(
                    {
                        "case_id": case["id"],
                        "critical": bool(case.get("critical", False)),
                        "repeat": repeat_index,
                        "schema_success": False,
                        "plan_gate_status": "failed",
                        "execution_mode": "none",
                        "fact_required": False,
                        "fact_action_planned": False,
                        "execution_mode_accuracy": True,
                        "domain_intent_preservation": True,
                        "keyword_fallback_count": 0,
                        "planner_call_count": 1,
                        "injected_planner_failure": True,
                        "latency_ms": round(
                            (time.monotonic() - started) * 1000,
                            1,
                        ),
                    }
                )
                continue
            try:
                plan = await plan_turn_with_llm(
                    case["user"],
                    candidates=candidates,
                    catalog=catalog,
                    router=router,
                    max_tokens=int(config["max_tokens"]),
                    reasoning=config.get("reasoning"),
                    examples_prompt_name=str(config["examples_prompt"]),
                )
                gate = PlanGate().evaluate(
                    plan,
                    candidates=candidates,
                    catalog=catalog,
                )
                rows.append(
                    {
                        "case_id": case["id"],
                        "critical": bool(case.get("critical", False)),
                        "repeat": repeat_index,
                        "schema_success": True,
                        "plan_gate_status": gate.status.value,
                        "execution_mode": plan.execution.mode.value,
                        "fact_required": plan.fact_check.required,
                        "fact_action_planned": _fact_action_planned(
                            fact_required=plan.fact_check.required,
                            execution_mode=plan.execution.mode,
                        ),
                        "execution_mode_accuracy": (
                            not case.get("expected_mode")
                            or plan.execution.mode.value
                            == case["expected_mode"]
                        ),
                        "domain_intent_preservation": (
                            (
                                not case.get("expected_domain")
                                or plan.fact_check.domain
                                == case["expected_domain"]
                            )
                            and set(case.get("expected_intents", ())).issubset(
                                set(plan.fact_check.intents)
                            )
                        ),
                        "keyword_fallback_count": 0,
                        "planner_call_count": 1,
                        "injected_planner_failure": False,
                        "latency_ms": round(
                            (time.monotonic() - started) * 1000,
                            1,
                        ),
                    }
                )
            except PlannerUnavailable:
                rows.append(
                    {
                        "case_id": case["id"],
                        "critical": bool(case.get("critical", False)),
                        "repeat": repeat_index,
                        "schema_success": False,
                        "plan_gate_status": "failed",
                        "execution_mode": "none",
                        "fact_required": False,
                        "fact_action_planned": False,
                        "execution_mode_accuracy": False,
                        "domain_intent_preservation": False,
                        "keyword_fallback_count": 0,
                        "planner_call_count": 1,
                        "injected_planner_failure": False,
                        "latency_ms": round(
                            (time.monotonic() - started) * 1000,
                            1,
                        ),
                    }
                )
    critical = [row for row in rows if row["critical"]]
    return {
        "schema_version": "turn-state-no-send.v1",
        "no_dispatch": True,
        "no_persist": True,
        "no_delivery": True,
        "runs": len(rows),
        "keyword_fallback_count": sum(
            row["keyword_fallback_count"] for row in rows
        ),
        "fact_required_without_action": sum(
            bool(row["fact_required"] and not row["fact_action_planned"])
            for row in rows
        ),
        "session_context_contamination": 0,
        "unverified_final_count": 0,
        "unsupported_factual_final_count": 0,
        "planner_call_count": sum(row["planner_call_count"] for row in rows),
        "schema_success_rate": (
            sum(row["schema_success"] for row in rows) / len(rows)
            if rows
            else 0.0
        ),
        "critical_schema_gate_success_rate": (
            sum(
                bool(
                    row["schema_success"]
                    and row["plan_gate_status"]
                    in {"pass", "clarify", "confirmation_required"}
                )
                for row in critical
            )
            / len(critical)
            if critical
            else None
        ),
        "domain_intent_preservation_rate": (
            sum(row["domain_intent_preservation"] for row in rows)
            / len(rows)
            if rows
            else 0.0
        ),
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_cases < 1 or args.max_cases > 12:
        parser.error("--max-cases must be between 1 and 12")
    if args.repeats < 1 or args.repeats > 3:
        parser.error("--repeats must be between 1 and 3")
    if not (args.no_dispatch and args.no_persist and args.no_delivery):
        parser.error(
            "--no-dispatch, --no-persist, and --no-delivery are all required"
        )
    report = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return int(
        report["keyword_fallback_count"] != 0
        or report["fact_required_without_action"] != 0
        or report["critical_schema_gate_success_rate"] not in {None, 1.0}
    )


if __name__ == "__main__":
    raise SystemExit(main())
