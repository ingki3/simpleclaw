#!/usr/bin/env python3
"""Configured provider로 BIZ-578 fixed-gold 시나리오를 평가한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _runtime_catalog(config_path: Path):
    """현재 config의 production discovery 경로로 planner catalog를 만든다."""
    from simpleclaw.agent.planner_catalog import build_planner_catalog
    from simpleclaw.agent.tool_schemas import (
        ToolScope,
        build_native_tool_registry,
    )
    from simpleclaw.config import load_recipes_config
    from simpleclaw.recipes.loader import discover_recipes
    from simpleclaw.skills.discovery import discover_skills

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    skills_config = raw.get("skills", {}) if isinstance(raw, dict) else {}
    if not isinstance(skills_config, dict):
        skills_config = {}
    local_dir = Path(str(skills_config.get("local_dir", ".agent/skills"))).expanduser()
    global_dir = Path(
        str(skills_config.get("global_dir", "~/.agents/skills"))
    ).expanduser()
    if not local_dir.is_absolute():
        local_dir = Path.cwd() / local_dir
    skills = discover_skills(local_dir, global_dir)
    recipes_dir = Path(load_recipes_config(config_path)["dir"]).expanduser()
    recipes = discover_recipes(recipes_dir)
    native_specs = tuple(
        replace(
            spec,
            definition=replace(
                spec.definition,
                description=spec.definition.description.replace("/", "／"),
            ),
        )
        for spec in build_native_tool_registry(scopes=(ToolScope.RUNTIME,))
    )
    catalog = build_planner_catalog(
        skills=skills,
        recipes=recipes,
        native_specs=native_specs,
    )
    return catalog, (*recipes, *skills), tuple(recipe.name for recipe in recipes)


async def _run(args: argparse.Namespace) -> int:
    from simpleclaw.evaluation.langgraph_v4_scenario_eval import (
        ConnectedContractProbe,
        ScenarioEvaluator,
        load_scenarios,
        render_markdown,
    )
    from simpleclaw.llm.router import create_router

    if not args.config.is_file():
        raise FileNotFoundError(f"config not found: {args.config}")
    cases = load_scenarios(args.fixture)
    catalog, definitions, recipe_names = _runtime_catalog(args.config)
    with tempfile.TemporaryDirectory(prefix="simpleclaw-v4-scenarios-") as tmp:
        probe = ConnectedContractProbe(definitions=definitions, directory=tmp)
        try:
            evaluator = ScenarioEvaluator(
                catalog=catalog,
                router=create_router(args.config),
                max_provider_calls=args.max_provider_calls,
                deadline_seconds=args.deadline_seconds,
                execute_read_only_contract_assets=(
                    args.execute_read_only_contract_assets
                ),
                connected_executor=probe,
                connected_executor_kind="synthetic_contract",
                ingress_recipe_names=recipe_names,
            )
            report = await evaluator.evaluate(
                cases,
                repeat_critical=args.repeat_critical,
            )
        finally:
            probe.close()
    counts = report["side_effect_counts"]
    if args.assert_zero_delivery and (
        counts["telegram_send"] or counts["cron_notifier"]
    ):
        raise RuntimeError("delivery side effect detected")
    if args.assert_zero_persistence and counts["conversation_write"]:
        raise RuntimeError("persistence side effect detected")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "provider_calls": report["summary"]["provider_calls"],
                "total_inventory_cases": report["summary"]["total_inventory_cases"],
                "scored_cases": report["summary"]["scored_cases"],
                "not_scored_cases": report["summary"]["not_scored_cases"],
                "scored_runs": report["summary"]["scored_runs"],
                "side_effect_counts": counts,
            },
            sort_keys=True,
        )
    )
    return 0 if report["decision"] == "go" else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/Users/simplist/.simpleclaw/config.yaml"),
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=REPO_ROOT / "tests/fixtures/langgraph_v4_user_scenarios.jsonl",
    )
    parser.add_argument("--repeat-critical", type=int, default=3)
    parser.add_argument("--max-provider-calls", type=int, default=64)
    parser.add_argument("--deadline-seconds", type=float, default=1200)
    parser.add_argument("--assert-zero-delivery", action="store_true")
    parser.add_argument("--assert-zero-persistence", action="store_true")
    parser.add_argument("--execute-read-only-contract-assets", action="store_true")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("langgraph_v4_user_scenario_results.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("langgraph_v4_user_scenario_analysis.md"),
    )
    return parser


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
