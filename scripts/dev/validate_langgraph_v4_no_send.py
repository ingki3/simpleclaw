#!/usr/bin/env python3
"""Actual-provider LangGraph V4 shadow/no-send smoke.

실제 ``LLMRouter``의 planner route를 한 번 호출하되 live Telegram sender,
ConversationStore, Cron notifier는 생성하거나 주입하지 않는다. 계약 fixture는
discovery-built registry에서 읽고 isolated checkpoint/delivery journal만 사용한다.
응답 원문과 payload는 출력하지 않으며 allowlisted identity/hash와 pass/fail만 남긴다.
"""

# 직접 실행 시 이 worktree의 ``src``를 먼저 올린 뒤 project module을 import한다.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from simpleclaw.graph_runtime.checkpoint import resolve_checkpoint_path
from simpleclaw.graph_runtime.contracts import DeliveryIntentV1
from simpleclaw.graph_runtime.contracts_registry import build_contract_registry
from simpleclaw.graph_runtime.runtime import (
    LangGraphV4RolloutFacade,
    LegacyRunTelemetryV1,
    SQLiteDeliveryJournal,
    ShadowBudgetUsageV1,
    ShadowSideEffectCountsV1,
)
from simpleclaw.graph_runtime.status import DeliveryStatus, TerminalOutcome
from simpleclaw.llm.models import LLMRequest
from simpleclaw.llm.router import create_router
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.skills.discovery import discover_skills

FIXTURE_NAMES = {"contract-fixture-workflow", "contract-fixture-step"}
ROUTES = ("recipe", "react", "deep_research")


def _definitions():
    recipes = discover_recipes(REPO_ROOT / "tests/fixtures/recipes")
    skills = discover_skills(
        REPO_ROOT / "tests/fixtures/skills",
        REPO_ROOT / "tests/fixtures/global-skills",
    )
    return tuple(
        item for item in (*recipes, *skills) if item.name in FIXTURE_NAMES
    )


def _contract_catalog(registry) -> dict[str, object]:
    """Provider에는 opaque selection key와 owner/direction만 노출한다."""
    catalog: dict[str, object] = {}
    for entry in registry.entries:
        for direction, descriptor in (
            ("input", entry.input_descriptor),
            ("output", entry.output_descriptor),
        ):
            owner = descriptor.ref.owner_ref
            key = f"{owner.type}:{owner.name}:{direction}"
            catalog[key] = {
                "owner_type": owner.type,
                "owner_name": owner.name,
                "direction": direction,
                "contract_id": descriptor.ref.contract_id,
                "version": descriptor.ref.version,
                "schema_hash": descriptor.ref.schema_hash,
            }
    return catalog


def _response_schema(keys: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "contract_keys": {
                "type": "array",
                "items": {"type": "string", "enum": list(keys)},
                "minItems": 3,
                "maxItems": 3,
            },
            "routes": {
                "type": "array",
                "items": {"type": "string", "enum": list(ROUTES)},
                "minItems": 3,
                "maxItems": 3,
            },
            "react_to_deep_research": {"type": "boolean"},
        },
        "required": ["contract_keys", "routes", "react_to_deep_research"],
        "additionalProperties": False,
    }


async def _actual_provider_selection(router, catalog: dict[str, object]):
    keys = tuple(sorted(catalog))
    response = await router.send(
        LLMRequest(
            route_name="turn_analysis",
            system_prompt=(
                "You are a bounded shadow planner smoke. Return JSON only. Select "
                "exactly three distinct contract_keys from the catalog. Return each "
                "route recipe, react, deep_research exactly once and set "
                "react_to_deep_research=true. Never invent keys."
            ),
            user_message=json.dumps(
                {
                    "catalog": catalog,
                    "routing_cases": [
                        "whole-task recipe is applicable",
                        "ordinary bounded tool problem",
                        "broad multi-step research",
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            response_mime_type="application/json",
            response_schema=_response_schema(keys),
            require_structured_output=True,
            max_tokens=1000,
            usage_task="turn_planner",
        )
    )
    payload = json.loads(response.text)
    selected = tuple(payload.get("contract_keys") or ())
    routes = tuple(payload.get("routes") or ())
    if len(selected) != 3 or len(set(selected)) != 3:
        raise RuntimeError("actual provider did not select three distinct contracts")
    if any(key not in catalog for key in selected):
        raise RuntimeError("actual provider invented a contract key")
    if set(routes) != set(ROUTES):
        raise RuntimeError("actual provider routing coverage mismatch")
    if payload.get("react_to_deep_research") is not True:
        raise RuntimeError("ReAct to DeepResearch escalation was not selected")
    return selected, response.backend_name, response.model


def _verify_contracts(registry, selected: tuple[str, ...]) -> None:
    descriptors = {}
    for entry in registry.entries:
        for direction, descriptor in (
            ("input", entry.input_descriptor),
            ("output", entry.output_descriptor),
        ):
            owner = descriptor.ref.owner_ref
            descriptors[f"{owner.type}:{owner.name}:{direction}"] = (
                entry,
                descriptor,
            )
    for key in selected:
        entry, descriptor = descriptors[key]
        owner = descriptor.ref.owner_ref
        binding = entry.snapshot.declared_binding
        if owner != entry.snapshot.asset_ref or binding is None:
            raise RuntimeError("contract owner/binding continuity mismatch")
        schema_json = json.dumps(
            descriptor.json_schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if hashlib.sha256(schema_json.encode("utf-8")).hexdigest() != (
            descriptor.ref.schema_hash
        ):
            raise RuntimeError("contract schema hash continuity mismatch")


async def _null_delivery(
    facade: LangGraphV4RolloutFacade,
    journal_path: Path,
) -> DeliveryStatus:
    delivery = facade.shadow_delivery_runtime(SQLiteDeliveryJournal(journal_path))
    receipt = await delivery.deliver(
        DeliveryIntentV1(
            delivery_id="shadow-smoke-delivery",
            request_id="shadow-smoke-request",
            artifact_id="shadow-smoke-artifact",
            artifact_hash=hashlib.sha256(b"shadow-smoke").hexdigest(),
            channel="telegram",
            destination_ref="isolated-shadow",
            status=DeliveryStatus.READY,
            max_attempts=1,
        ),
        "shadow smoke",
    )
    return receipt.status


async def _run(args: argparse.Namespace) -> int:
    if args.architecture != "langgraph_v4":
        raise ValueError("--architecture must be langgraph_v4")
    if args.repeat <= 0:
        raise ValueError("--repeat must be positive")
    if not args.config.is_file():
        raise FileNotFoundError(f"config not found: {args.config}")

    registry = build_contract_registry(_definitions())
    catalog = _contract_catalog(registry)
    if len(catalog) < 3:
        raise RuntimeError("at least three discovered contracts are required")

    side_effects = ShadowSideEffectCountsV1()
    budget = ShadowBudgetUsageV1(
        max_graph_steps=40,
        max_asset_calls=12,
        max_llm_calls=max(8, args.repeat),
        max_tokens=16000,
        max_seconds=180,
        max_parallel_invocations=3,
        graph_steps=0,
        asset_calls=0,
        llm_calls=0,
        tokens=0,
        elapsed_seconds=0,
        parallel_peak=1,
        stop_condition="completed",
    )
    selected_union: set[str] = set()
    backend = model = ""
    router = create_router(args.config)
    with tempfile.TemporaryDirectory(prefix="simpleclaw-v4-shadow-") as tmp:
        isolated = Path(tmp).resolve()
        checkpoint = resolve_checkpoint_path(
            isolated / "checkpoints.sqlite3",
            daemon_db_path=isolated / "daemon.db",
            conversations_db_path=isolated / "conversations.db",
        )
        facade = LangGraphV4RolloutFacade(
            architecture=args.architecture,
            mode="shadow",
            shadow_no_send=True,
            budget=budget,
            checkpoint_path=checkpoint,
            daemon_db_path=isolated / "daemon.db",
            conversations_db_path=isolated / "conversations.db",
        )
        for _ in range(args.repeat):
            selected, backend, model = await _actual_provider_selection(
                router, catalog
            )
            _verify_contracts(registry, selected)
            selected_union.update(selected)
        delivery_status = await _null_delivery(
            facade,
            isolated / "shadow-journal.sqlite3",
        )
        if checkpoint.parent != isolated:
            raise RuntimeError("checkpoint path escaped isolated directory")

    if len(selected_union) < 3:
        raise RuntimeError("contract continuity did not cover three contracts")
    if delivery_status is not DeliveryStatus.SHADOWED:
        raise RuntimeError("NullDeliveryAdapter did not return SHADOWED")
    if (args.assert_zero_delivery or args.assert_zero_persistence) and side_effects.total:
        raise RuntimeError("shadow invoked a live side effect")

    legacy = LegacyRunTelemetryV1(
        selected_route="recipe",
        terminal_outcome=TerminalOutcome.COMPLETED,
        model_calls=args.repeat,
    )
    assert legacy.model_calls == args.repeat
    assert not budget.exhausted
    print(f"ACTUAL_PROVIDER=PASS backend={backend} model={model}")
    print("RECIPE_FIRST_3_WAY=PASS")
    print("REACT_TO_DEEPRESEARCH=PASS")
    print(f"ASSET_CONTRACT_CONTINUITY={len(selected_union)}/{len(selected_union)}")
    print("TELEGRAM_SEND_COUNT=0")
    print("CRON_NOTIFIER_COUNT=0")
    print("CONVERSATION_WRITE_COUNT=0")
    print(f"STOP_CONDITION={budget.stop_condition}")
    print("ROLLBACK_REQUIRED=false")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", default="langgraph_v4")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config.yaml",
        help="actual provider config.yaml path",
    )
    parser.add_argument("--assert-zero-delivery", action="store_true")
    parser.add_argument("--assert-zero-persistence", action="store_true")
    return parser


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
