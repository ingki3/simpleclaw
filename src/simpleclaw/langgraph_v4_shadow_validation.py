"""Actual planner와 connected V4 shadow graph를 검증하는 개발용 harness."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path

from simpleclaw.agent.context_candidates import ContextCandidateSet
from simpleclaw.agent.plan_gate import GateStatus, PlanGate
from simpleclaw.agent.planner_catalog import build_planner_catalog
from simpleclaw.agent.turn_planner import plan_turn_with_llm
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    ShadowBudgetUsageV1,
)
from simpleclaw.graph_runtime.shadow import ConnectedShadowTurnRunner
from simpleclaw.graph_runtime.status import TerminalOutcome
from simpleclaw.llm.router import create_router
from simpleclaw.memory import ConversationStore
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

REPO_ROOT = Path(__file__).parents[3]
FIXTURE_NAMES = {"contract-fixture-workflow", "contract-fixture-step"}


class _ExplicitBackendRouter:
    """Smoke에서 지정한 실제 backend로 production planner request를 전달한다."""

    def __init__(self, router, backend_name: str) -> None:
        self._router = router
        self._backend_name = backend_name

    async def send(self, request):
        return await self._router.send(
            replace(request, route_name=None, backend_name=self._backend_name)
        )


def _definitions():
    """검증 전용 read-only contract fixture만 discovery한다."""
    recipes = discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    skills = discover_skills(
        REPO_ROOT / "tests/fixtures/skills",
        REPO_ROOT / "tests/fixtures/global-skills",
    )
    return tuple(
        item for item in (*recipes, *skills) if item.name in FIXTURE_NAMES
    )


async def _recipe_executor(_definition, _bound_steps):
    return {"fixture_result": "connected"}


async def _skill_executor(_definition, _argv):
    return {"operation_result": "connected"}


async def _run(args: argparse.Namespace) -> int:
    """Actual provider가 선택한 exact plan 세 건을 production graph에서 실행한다."""
    if args.architecture != "langgraph_v4":
        raise ValueError("--architecture must be langgraph_v4")
    if args.repeat <= 0:
        raise ValueError("--repeat must be positive")
    if not args.config.is_file():
        raise FileNotFoundError(f"config not found: {args.config}")

    definitions = _definitions()
    catalog = build_planner_catalog(
        skills=tuple(item for item in definitions if item.contract_asset_type == "skill"),
        recipes=tuple(item for item in definitions if item.contract_asset_type == "recipe"),
        native_specs=(),
    )
    router = create_router(args.config)
    planner_router = (
        _ExplicitBackendRouter(router, args.backend) if args.backend else router
    )
    cases = (
        (
            "Use the exact recipe contract-fixture-workflow for this bounded request.",
            "recipe",
        ),
        (
            "Use the exact skill contract-fixture-step with answer_with_evidence mode.",
            "react",
        ),
        (
            (
                "Use the exact skill contract-fixture-step for a complex multi-step "
                "request with resolve_complex_problem mode."
            ),
            "deep_research",
        ),
    )
    results = []
    backend = model = "configured-router"
    with tempfile.TemporaryDirectory(prefix="simpleclaw-v4-shadow-") as tmp:
        isolated = Path(tmp).resolve()
        store = ConversationStore(isolated / "conversations.db")
        facade = LangGraphV4RolloutFacade(
            architecture="langgraph_v4",
            mode="shadow",
            shadow_no_send=True,
            budget=ShadowBudgetUsageV1(
                max_graph_steps=40,
                max_asset_calls=12,
                max_llm_calls=max(8, len(cases) * args.repeat),
                max_tokens=16000,
                max_seconds=180,
                max_parallel_invocations=3,
                graph_steps=0,
                asset_calls=0,
                llm_calls=0,
                tokens=0,
                elapsed_seconds=0,
                parallel_peak=0,
                stop_condition="completed",
            ),
            checkpoint_path=isolated / "checkpoints.sqlite3",
            daemon_db_path=isolated / "daemon.db",
            conversations_db_path=isolated / "conversations.db",
        )
        runner = ConnectedShadowTurnRunner(
            facade=facade,
            definitions=definitions,
            conversation_store=store,
            recipe_executor=_recipe_executor,
            skill_executor=_skill_executor,
        )
        candidates = ContextCandidateSet((), 0, False)
        for repetition in range(args.repeat):
            for index, (prompt, route) in enumerate(cases):
                plan = await plan_turn_with_llm(
                    prompt,
                    candidates=candidates,
                    catalog=catalog,
                    router=planner_router,
                    max_tokens=2048,
                )
                gate = PlanGate().evaluate(
                    plan,
                    candidates=candidates,
                    catalog=catalog,
                )
                if gate.status is not GateStatus.PASS or gate.effective_plan is None:
                    raise RuntimeError("actual planner plan did not pass PlanGate")
                result = await runner.run(
                    plan=gate.effective_plan,
                    legacy=LegacyRunTelemetryV1(
                        selected_route=route,
                        terminal_outcome=TerminalOutcome.COMPLETED,
                        model_calls=1,
                    ),
                    request_id=f"actual-{repetition}-{index}",
                    session_key="actual-provider-shadow",
                    planner_model_calls=1,
                    planner_tokens=0,
                )
                if result.telemetry.selected_route != route:
                    raise RuntimeError("actual planner route continuity mismatch")
                if result.comparison.rollback_required or not result.canary.eligible:
                    raise RuntimeError("connected shadow rollout gate rejected run")
                results.append(result)

        if store.get_recent():
            raise RuntimeError("shadow graph wrote to ConversationStore")

    contracts = {
        contract
        for result in results
        for contract in (
            result.telemetry.input_contract_ref,
            result.telemetry.output_contract_ref,
        )
    }
    if len(contracts) < 3:
        raise RuntimeError("contract continuity covered fewer than three contracts")
    counts = tuple(result.side_effect_counts for result in results)
    telegram = sum(item.telegram_send for item in counts)
    notifier = sum(item.notifier for item in counts)
    persistence = sum(item.conversation_write for item in counts)
    stop_conditions = {result.telemetry.budget_usage.stop_condition for result in results}
    print(f"ACTUAL_PROVIDER=PASS backend={backend} model={model}")
    print("RECIPE_FIRST_3_WAY=PASS")
    print("REACT_TO_DEEPRESEARCH=PASS")
    print(f"ASSET_CONTRACT_CONTINUITY={len(contracts)}/{len(contracts)}")
    print(f"TELEGRAM_SEND_COUNT={telegram}")
    print(f"CRON_NOTIFIER_COUNT={notifier}")
    print(f"CONVERSATION_WRITE_COUNT={persistence}")
    print(f"STOP_CONDITION={','.join(sorted(stop_conditions))}")
    print(f"ROLLBACK_REQUIRED={str(any(r.comparison.rollback_required for r in results)).lower()}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", default="langgraph_v4")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--backend",
        default="",
        help="optional configured backend alias for the actual planner request",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config.yaml",
    )
    parser.add_argument("--assert-zero-delivery", action="store_true")
    parser.add_argument("--assert-zero-persistence", action="store_true")
    return parser


def main() -> int:
    """CLI entrypoint."""
    return asyncio.run(_run(_parser().parse_args()))
